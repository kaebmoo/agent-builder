#!/usr/bin/env python3
"""Render launchd/systemd launchers for an Atlas export (M18a).

Reads atlas-register.json only: daemon CWD, port and bind from worker.base_url, and env NAMES. No value of any
secret is ever read or written; the operator fills <name>.env on the host. Rendering is deterministic.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates" / "deploy"
# Literal, non-secret daemon env: keep the single-agent layout (DESIGN §4 M13 (b)) and skip the macOS keychain
# consent dialog on a headless start (DESIGN §4 M17 item 9).
FIXED_ENV = {"THCLAWS_AUTO_MIGRATE": "0", "THCLAWS_DISABLE_KEYCHAIN": "1"}
# Names the env file must define before deployment.env: the token Atlas registers and the MCP spawn switch.
DAEMON_ENV = ("THCLAWS_API_TOKEN", "THCLAWS_MCP_ALLOW_ALL")
DEFAULT_PORT = {"http": 80, "https": 443}
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def context(register: dict, *, thclaws_bin: str, env_file: str | None) -> dict:
    deployment, name = register["deployment"], register["package"]["name"]
    parts = urlsplit(register["worker"]["base_url"])
    return {
        "name": name,
        "label": f"thclaws.{name}",
        "archive": register["package"]["archive"],
        "package_root": deployment["package_root"],
        "daemon_cwd": deployment["daemon_cwd"],
        # The daemon listens where Atlas calls; put a reverse proxy in front if that port is not the daemon's.
        "port": parts.port or DEFAULT_PORT[parts.scheme],
        "bind": "127.0.0.1" if parts.hostname in LOOPBACK else "0.0.0.0",
        "thclaws_bin": thclaws_bin,
        "env_file": env_file or f"/etc/thclaws/{name}.env",
        "fixed_env": FIXED_ENV,
        "env_names": [*DAEMON_ENV, *deployment["env"]],
        "mcp_servers": deployment["mcp_servers"],
        "model": register["node_template"]["model"],
    }


def render(export_dir: Path, *, thclaws_bin: str = "/usr/local/bin/thclaws", env_file: str | None = None) -> dict[str, str]:
    register = json.loads((Path(export_dir) / "atlas-register.json").read_text(encoding="utf-8"))
    values = context(register, thclaws_bin=thclaws_bin, env_file=env_file)
    # The plist is XML, so values rendered into it are escaped; the unit and env files are not markup.
    jinja = Environment(loader=FileSystemLoader(TEMPLATES), undefined=StrictUndefined, keep_trailing_newline=True,
                        trim_blocks=True, lstrip_blocks=True,
                        autoescape=select_autoescape(enabled_extensions=("plist.j2",),
                                                     disabled_extensions=("service.j2", "example.j2")))
    return {f"{values['name']}.service": jinja.get_template("thclaws.service.j2").render(values),
            f"{values['label']}.plist": jinja.get_template("thclaws.plist.j2").render(values),
            f"{values['name']}.env.example": jinja.get_template("env.example.j2").render(values)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_dir", type=Path,
                        help="forge export directory holding atlas-register.json (under the working directory)")
    parser.add_argument("--out", required=True, type=Path,
                        help="new directory for the rendered files (under the working directory)")
    parser.add_argument("--thclaws-bin", default="/usr/local/bin/thclaws", help="desktop-build binary path on the host")
    parser.add_argument("--env-file", help="env file the launcher reads on the host (default /etc/thclaws/<name>.env)")
    args = parser.parse_args()
    if args.out.exists() or args.out.is_symlink():
        parser.exit(1, f"render_launcher FAIL: destination already exists: {args.out}\n")
    # ponytail: both paths must sit under the working directory (the runbook uses out/...); relax if a layout needs it.
    cwd = os.path.realpath(os.getcwd()) + os.sep
    export_dir, out = os.path.realpath(args.export_dir), os.path.realpath(args.out)
    if not (export_dir.startswith(cwd) and out.startswith(cwd)):
        parser.exit(1, f"render_launcher FAIL: paths must be inside {os.getcwd()}: {args.export_dir}, {args.out}\n")
    files = render(Path(export_dir), thclaws_bin=args.thclaws_bin, env_file=args.env_file)
    Path(out).mkdir(parents=True)
    for filename, text in sorted(files.items()):
        (Path(out) / filename).write_text(text, encoding="utf-8")
    print(f"Rendered {', '.join(sorted(files))} into {args.out}; fill the env file on the host, never in the repo.")
    print("These launchers start the daemon only; deployment_verified is never set by the builder.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
