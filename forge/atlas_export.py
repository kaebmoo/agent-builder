"""Atlas registration export (M8). Operators supply deployment bindings; nothing here verifies a deployment."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from jsonschema import Draft202012Validator, ValidationError

from forge.audit import static_findings, thclaws_validate
from forge.generate import (
    REPORT_SCHEMA_PATH,
    ROOT,
    THCLAWS_BASELINE,
    input_envelope,
    json_text,
    t2_node_template,
    worker_outputs,
)
from forge.spec import ATLAS_TARGETS

# atlas-control-plane revision whose docs/specs/workflow-definition.schema.json is pinned byte-for-byte.
ATLAS_REF = "daa0f4966899327fb563501435f5d6180eeef9c3"
ATLAS_SCHEMA_PATH = ROOT / "patterns" / "atlas-workflow-definition.schema.json"
REGISTER, WORKFLOW = "atlas-register.json", "atlas-workflow.json"
# Edge-template placeholder: the upstream node whose collect_files artifacts feed this worker.
UPSTREAM = "__UPSTREAM_NODE_ID__"
T2_PLACEHOLDERS = {"__OPERATOR_T2_WORKER_ID__": "worker_id", "__OPERATOR_T2_WORKSPACE_ID__": "workspace_id"}
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
FILES_DIR_LINE = "\nHandoff files directory: {files_dir}"
# thclaws agent pack (v0.116.0 revision 75edc48) rewrites exactly these two files; every other member is byte-identical.
ARCHIVE_REWRITTEN = ("manifest.json", ".thclaws/settings.json")
NOT_VERIFIED = ("These checks prove reachability and MCP inventory only. Daemon isolation, egress control and the "
                "approval path are verified by the operator outside this file; the builder never sets deployment_verified.")


def bind(spec: dict[str, Any], *, base_url: str, workspace_dir: str, worker_id: str | None = None,
         workspace_id: str | None = None, workspace_key: str | None = None) -> dict[str, str]:
    """Validate operator-supplied deployment-time values (DESIGN §3). None of them is a secret."""
    base_url = base_url.strip()
    parts = urlsplit(base_url)
    try:
        port = parts.port  # ValueError for a non-numeric or out-of-range port
    except ValueError:
        port = 0
    host_part = parts.netloc.rpartition("@")[2].rpartition("]")[2]
    valid_port = (port is None and ":" not in host_part) or bool(port)  # port 0 is not routable
    if (not base_url.startswith(("http://", "https://")) or not parts.hostname or not valid_port
            or parts.username or parts.password or "?" in base_url or "#" in base_url):
        raise ValueError(f"base_url must be a plain http(s) URL without credentials, query or fragment: {base_url!r}")
    directory = PurePosixPath(workspace_dir)
    if not directory.is_absolute() or ".." in directory.parts:
        raise ValueError(f"workspace_dir must be an absolute path on the worker host: {workspace_dir!r}")
    name = spec["identity"]["name"]
    binding = {"base_url": urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", "")),
               "workspace_dir": str(directory),
               "worker_id": worker_id or f"wrk_{name}", "workspace_id": workspace_id or f"wsp_{name}",
               "workspace_key": workspace_key or name}
    for key in ("worker_id", "workspace_id", "workspace_key"):
        if not ID_PATTERN.fullmatch(binding[key]):
            raise ValueError(f"{key} must match {ID_PATTERN.pattern}: {binding[key]!r}")
    return binding


def handoff_inputs(spec: dict[str, Any]) -> bool:
    return any(item["transport"] == "atlas_file_handoff" for item in spec["inputs"])


def worker_node(spec: dict[str, Any], binding: dict[str, str]) -> dict[str, Any]:
    """T0/T1 node: the audited JSON input envelope as prompt, plus Atlas's {files_dir} for file handoff."""
    prompt = input_envelope(spec) + (FILES_DIR_LINE if handoff_inputs(spec) else "")
    return {"id": spec["identity"]["name"], "type": "worker", "role": spec["routing"]["role"],
            "tags": spec["routing"]["tags"], "worker_id": binding["worker_id"],
            "workspace_id": binding["workspace_id"], "model": spec["model"]["id"], "prompt": prompt,
            **worker_outputs(spec)}


def rebind(value: Any, binding: dict[str, str]) -> Any:
    """Replace T2 placeholders structurally (whole string values only), never by text substitution."""
    if isinstance(value, dict):
        return {key: rebind(item, binding) for key, item in value.items()}
    if isinstance(value, list):
        return [rebind(item, binding) for item in value]
    return binding[T2_PLACEHOLDERS[value]] if isinstance(value, str) and value in T2_PLACEHOLDERS else value


def templates(spec: dict[str, Any], binding: dict[str, str]) -> tuple[dict, dict | None, dict, dict | None]:
    """Return (node, edge, policy, workflow). Raises ValueError for shapes Atlas cannot run."""
    if handoff_inputs(spec) and spec["target"] == "atlas-workflow":
        raise ValueError("a single-node atlas-workflow cannot receive push_files; export the package as "
                         "atlas-worker and compose edge_template after an upstream node with collect_files")
    if spec["permissions"]["tier"] == "T2":
        flow = rebind(t2_node_template(spec)["workflow"], binding)
        node = next(item for item in flow["graph"]["nodes"] if item["type"] == "worker")
        return node, None, flow["policy"], flow
    node = worker_node(spec, binding)
    policy = {"allowed_worker_ids": [binding["worker_id"]], "allowed_workspace_ids": [binding["workspace_id"]]}
    edge = None
    if handoff_inputs(spec):
        policy["file_handoff"] = True
        edge = {"from": UPSTREAM, "to": node["id"], "condition": {"type": "always"},
                "push_files": [f"files.{UPSTREAM}.*"]}
    flow = None
    if spec["target"] == "atlas-workflow":
        flow = {"name": node["id"], "graph": {"start": node["id"], "nodes": [node], "edges": []}, "policy": policy}
    return node, edge, policy, flow


def render_export(spec: dict[str, Any], report: dict[str, Any], binding: dict[str, str],
                  archive: str | None) -> dict[str, bytes]:
    """Render the export files in memory; deterministic for one package and one binding."""
    if spec["target"] not in ATLAS_TARGETS:
        raise ValueError("Atlas export supports atlas-worker and atlas-workflow packages only")
    node, edge, policy, flow = templates(spec, binding)
    schema = json.loads(ATLAS_SCHEMA_PATH.read_text(encoding="utf-8"))
    for fragment, definition in ((node, "workerNode"), (edge, "edge"), (policy, "policy")):
        if fragment is not None:
            Draft202012Validator({"$defs": schema["$defs"], "$ref": f"#/$defs/{definition}"}).validate(fragment)
    if flow is not None:
        Draft202012Validator(schema).validate(flow)
    identity, dependencies = spec["identity"], report["dependencies"]
    servers = sorted({server for pack in dependencies for server in pack["mcp_servers"]})
    worker = {"id": binding["worker_id"], "name": identity["name"], "base_url": binding["base_url"],
              "role": spec["routing"]["role"], "tags": spec["routing"]["tags"]}
    workspace = {"id": binding["workspace_id"], "worker_id": binding["worker_id"],
                 "workspace_key": binding["workspace_key"], "workspace_dir": binding["workspace_dir"],
                 "tags": spec["routing"]["tags"]}
    register = {
        "format_version": 1,
        "atlas_ref": ATLAS_REF,
        "package": {"name": identity["name"], "version": identity["version"], "target": spec["target"],
                    "tier": spec["permissions"]["tier"], "package_status": report["package_status"],
                    "audit": report["audit"], "archive": archive},
        "worker": worker,
        "workspace": workspace,
        "node_template": node,
        "edge_template": edge,
        "policy_template": policy,
        "workflow": WORKFLOW if flow else None,
        "deployment": {
            "package_root": binding["workspace_dir"],
            "daemon_cwd": binding["workspace_dir"],
            "isolated_daemon_required": spec["permissions"]["tier"] == "T2",
            "deployment_verified": False,
            "env": sorted(spec["env"]),
            "network": spec["permissions"]["network"],
            "hosts": sorted({host for pack in dependencies for host in pack.get("hosts", [])}),
            "mcp_servers": servers,
            "packs": [{key: value for key, value in pack.items() if key != "conformance"} for pack in dependencies],
            "reachability_checks": [
                f"POST /api/workers/{worker['id']}/poll must return status online",
                (f"agent_info.agent.mcp_servers names must equal [{', '.join(servers) or 'none'}] "
                 f"(GET {worker['base_url']}/v1/agent/info)"),
                NOT_VERIFIED,
            ],
            "hints": report["deployment_hints"],
        },
        "registration": [
            ("Unpack the archive at deployment.package_root on the worker host; start thclaws --serve there with "
             "THCLAWS_API_TOKEN and the deployment.env names set (values never live in this file)."),
            (f"The unpacked tree differs from the audited package only in {' and '.join(ARCHIVE_REWRITTEN)} "
             "(thclaws agent pack fuses the identity manifest and strips the settings agent block); run audit.py "
             "against the source package, not the unpacked copy."),
            ("POST /api/workers (admin token): body = worker plus token = the daemon THCLAWS_API_TOKEN. Atlas upserts "
             "by id or base_url, so one daemon URL maps to exactly one worker."),
            "POST /api/workspaces: body = workspace.",
            f"POST /api/workflows: body = {WORKFLOW}." if flow else
            (f"Compose node_template, edge_template and policy_template into a workflow; replace {UPSTREAM} "
             "with the id of the node whose collect_files feed this worker."),
            ("Run deployment.reachability_checks before any production run; package_status is copied from the "
             "build report and export never promotes it."),
        ],
    }
    files = {REGISTER: json_text(register).encode("utf-8")}
    if flow:
        files[WORKFLOW] = json_text(flow).encode("utf-8")
    return files


def pack_archive(binary: str, package: Path, target: Path, expected: set[str]) -> None:
    """Delegate packing to thClaws (DESIGN §1), then prove the archive is the audited package.

    thclaws agent pack silently strips paths containing `_secret` and files ending in .env/.key/.log/.pyc
    (pack.rs), so a pack script such as client_secret.py or an input named api_secret would deploy without its
    file while the run continues without that MCP (DESIGN §4). Members must equal the report's generated
    files and match the package byte-for-byte except ARCHIVE_REWRITTEN.
    """
    version = subprocess.run([binary, "--version"], capture_output=True, text=True, check=True, timeout=30)
    if version.stdout.split()[:2] != ["thclaws", THCLAWS_BASELINE]:
        first = (version.stdout.strip().splitlines() or ["<no output>"])[0]
        raise ValueError(f"{first} is not the verified baseline {THCLAWS_BASELINE}")
    result = subprocess.run([binary, "agent", "pack", str(package), "--out", str(target)],
                            capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0 or not target.is_file():
        raise ValueError("thclaws agent pack failed: " + (result.stdout + result.stderr).strip())
    with tarfile.open(target, mode="r:gz") as archive:
        members = {member.name: archive.extractfile(member).read() for member in archive.getmembers()
                   if member.isfile()}
    missing, extra = sorted(expected - set(members)), sorted(set(members) - expected)
    if missing or extra:
        raise ValueError(f"archive inventory differs from the audited package: missing {missing}, extra {extra}; "
                         "thclaws agent pack strips *_secret*, *.env, *.key, *.log and *.pyc silently, so rename "
                         "the asset or input/output and regenerate")
    changed = sorted(path for path in members
                     if path not in ARCHIVE_REWRITTEN and members[path] != (package / path).read_bytes())
    if changed:
        raise ValueError(f"archive content differs from the audited package: {changed}; "
                         "thclaws agent pack behaviour changed, re-check DESIGN §8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_name(target.name + ".sha256").write_text(f"{digest}  {target.name}\n", encoding="utf-8")


def export(package: Path, out: Path, *, packs_dir: Path | None = None, **binding_values: str | None
           ) -> tuple[dict[str, Any], int]:
    """Write atlas-register.json, the workflow (atlas-workflow only) and the thClaws archive into a new directory.

    Returns (register document, exit code): 0, or 2 when thclaws is absent and the archive was skipped.
    Refuses: static-audit errors, a report that is stale, schema-invalid or does not record a passed static
    audit, a manifest that fails `thclaws agent validate` when the binary is present, bad bindings, and a
    destination that exists or lies inside the package (thclaws agent pack would archive the staging directory).
    """
    package, out = Path(package).resolve(), Path(out)
    if out.exists() or out.is_symlink():
        raise FileExistsError(f"destination already exists: {out}")
    if out.resolve().is_relative_to(package):
        raise ValueError(f"export directory must be outside the package: {out}")
    spec = json.loads((package / "agentspec.json").read_text(encoding="utf-8"))
    findings, rendered = static_findings(package, packs_dir)
    errors = [finding["message"] for finding in findings if finding["severity"] == "error"]
    if rendered is None or errors:
        raise ValueError("static audit errors block export: " + "; ".join(errors))
    report = json.loads((package / "builder-build-report.json").read_text(encoding="utf-8"))
    try:
        Draft202012Validator(json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))).validate(report)
    except ValidationError as exc:
        raise ValueError(f"builder-build-report.json does not satisfy its schema: {exc.message}") from exc
    if any(report[key] != rendered[key] for key in ("dependencies", "deployment_hints", "generated_files")):
        raise ValueError("builder-build-report.json is stale; rerun studio.py or forge audit --write first")
    if report["audit"]["static"] != "passed" or report["audit"]["manifest"] == "failed":
        raise ValueError(f"builder-build-report.json records audit.static={report['audit']['static']} "
                         f"audit.manifest={report['audit']['manifest']}; run forge audit --write first")
    binding = bind(spec, **binding_values)
    binary = shutil.which(os.environ.get("THCLAWS_BIN", "thclaws"))
    if binary and report["audit"]["manifest"] != "passed":
        validate = thclaws_validate(package)  # the recorded audit ran without the binary; never pack unvalidated
        if validate["status"] != "passed":
            raise ValueError(f"thclaws agent validate {validate['status']}: {validate['detail']}")
    archive = f"{spec['identity']['name']}-{spec['identity']['version']}.tar.gz" if binary else None
    files = render_export(spec, report, binding, archive)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".forge-export-", dir=out.parent) as temporary:
        staged = Path(temporary) / "export"
        staged.mkdir()
        for path, content in sorted(files.items()):
            (staged / path).write_bytes(content)
        if binary:
            pack_archive(binary, package, staged / archive, set(report["generated_files"]))
        if out.exists() or out.is_symlink():
            raise FileExistsError(f"destination already exists: {out}")
        staged.rename(out)
    return json.loads(files[REGISTER]), 0 if binary else 2


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("package", type=Path, help="statically audited package directory")
    parser.add_argument("--out", required=True, type=Path,
                        help="new export directory outside the package; existing paths are refused")
    parser.add_argument("--base-url", required=True, help="daemon URL that Atlas will call")
    parser.add_argument("--workspace-dir", required=True,
                        help="absolute package directory on the worker host; the daemon must start there")
    parser.add_argument("--worker-id", help="Atlas worker id (default wrk_<name>)")
    parser.add_argument("--workspace-id", help="Atlas workspace id (default wsp_<name>)")
    parser.add_argument("--workspace-key", help="Atlas workspace key (default <name>)")


def command(args: argparse.Namespace) -> int:
    try:
        register, code = export(args.package, args.out, base_url=args.base_url, workspace_dir=args.workspace_dir,
                                worker_id=args.worker_id, workspace_id=args.workspace_id,
                                workspace_key=args.workspace_key)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError, ValidationError) as exc:
        print(f"export FAIL: {exc}")
        return 1
    package = register["package"]
    print(f"Exported {args.out}: package_status={package['package_status']}; "
          f"workflow={register['workflow'] or 'none (compose the templates)'}; "
          f"archive={package['archive'] or 'skipped, thclaws not on PATH (SKIP)'}")
    print("Registration is an operator step. deployment_verified is never set by the builder; "
          "the Atlas poll proves reachability and MCP inventory only.")
    return code
