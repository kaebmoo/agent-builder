"""Pack authoring helpers (M10): scaffold a contract-shaped pack and draft `pack.yaml` from a live `tools/list`.

Neither command decides tier, network, env, mutating or fixture for the author. A scaffold or a draft is
deliberately not conformant: `forge pack test` fails with a readable reason until every TODO is replaced.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from forge.packs import digest

SERVER_NAME = re.compile(r'[a-z0-9][a-z0-9-]*')
TOOL_NAME = re.compile(r'[a-z0-9][a-z0-9_]*')
ENV_NAME = re.compile(r'[A-Z][A-Z0-9_]*')
TODO = 'TODO'

SETUP_TEMPLATE = '''"""Fixture for the {name} pack: build synthetic data under argv[1] and print the declared env values as JSON.

Rules (docs/packs.md): no real data, no network, no credentials. The harness owns and deletes argv[1].
Exit 2 (not 1) when the MCP runtime is unavailable on this machine, so `forge pack test` reports an explicit skip.
Probe the runtime with the same interpreter the server command uses (python3 on PATH), not sys.executable.
"""
import json
import sys
from pathlib import Path

work = Path(sys.argv[1]).resolve()
# TODO: create synthetic fixture data under `work`, then map every name in pack.yaml `env` to its fixture value.
values = {{}}
print(json.dumps(values))
'''

README_TEMPLATE = '''# {name} pack

Scaffolded by `forge pack new {name}`; follows the pack contract in `docs/packs.md` (DESIGN §5).

Fill every `TODO` in `pack.yaml`, implement the MCP server under `scripts/` (stdio JSON-RPC: `initialize`,
`tools/list`, `tools/call`; stdlib or a runtime the deployer installs), make `fixture/setup.py` produce
synthetic data, then run:

```bash
forge pack test {name}
```

The pack is `conformant` only when that command exits 0. Secrets never live in this directory: declare
env **names** in `pack.yaml` and let the operator set values on the daemon process.
A mutating tool requires `min_tier: T2` plus explicit `dry_run` and `idempotency_key` inputs; the server,
not the prompt, must reject invalid or mutating requests (`expect: error` cases).
'''


def scaffold_pack(name: str, packs_dir: Path) -> list[str]:
    """Create packs/<name>/ with a TODO-marked v2 descriptor, fixture skeleton and README. Never overwrites."""
    if not SERVER_NAME.fullmatch(name):
        raise ValueError('pack name must match ^[a-z0-9][a-z0-9-]*$ (it becomes the MCP server prefix)')
    folder = Path(packs_dir) / name
    if folder.exists() or folder.is_symlink():
        raise FileExistsError(f'pack directory already exists: {folder}')
    descriptor = f'''# Pack contract v2 (docs/packs.md). `forge pack test {name}` fails until every TODO is replaced.
min_tier: {TODO}          # T0 | T1 | T2 — T2 is mandatory when any tool is mutating
network: {TODO}           # none | allowlist | general-http — the minimum the server needs
hosts: []               # required (non-empty) only for allowlist; deployment egress hint
env: []                 # env NAMES the daemon process must provide; values never live in files
tools: []               # built-in thClaws tools the pack also needs (for example Read)
mcp_servers:
  - name: {name}
    command: python3
    args: [scripts/server.py]   # scripts/<file>.py is relocated into the generated package
    tools: [{TODO}]             # every tool the server advertises, bare names, nothing missing or extra
    mutating: []                # tools with side effects; non-empty forces min_tier T2
scripts: [server.py]    # implement scripts/server.py (stdio JSON-RPC MCP)
skills: []              # optional skills/<skill>/SKILL.md bundled into the package
fixture:
  setup: fixture/setup.py
  cases:                # at least one expect: ok and one expect: error per server, run without an LLM
    - {{tool: {name}__{TODO}, args: {{}}, expect: ok}}
    - {{tool: {name}__{TODO}, args: {{}}, expect: error}}
'''
    files = {
        'pack.yaml': descriptor,
        'fixture/setup.py': SETUP_TEMPLATE.format(name=name),
        'README.md': README_TEMPLATE.format(name=name),
    }
    folder.mkdir(parents=True)
    try:
        for relative, content in files.items():
            target = folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8')
        (folder / 'scripts').mkdir()
    except OSError:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return sorted(files)


def clean_env(names: list[str]) -> dict[str, str]:
    """PATH plus the named variables from this process; a missing name is an error, never an empty value."""
    env = {'PATH': os.environ.get('PATH', os.defpath)}
    for name in names:
        if not ENV_NAME.fullmatch(name):
            raise ValueError(f'invalid env name: {name!r}')
        if name == 'PATH' or name not in os.environ:
            raise ValueError(f'env {name} is not set in this process (PATH is reserved)')
        env[name] = os.environ[name]
    return env


def tool_inventory(server: str, command: str, args: list[str], env: dict[str, str], timeout: float) -> list[dict]:
    """initialize → tools/list against the real server, with the harness's own RPC client and clean env."""
    from forge.pack_test import RPC

    if shutil.which(command, path=env['PATH']) is None:
        raise ValueError(f'runtime not found on PATH: {command}')
    rpc = RPC([command, *args], os.getcwd(), env, timeout)
    try:
        initialized = rpc.call('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                             'clientInfo': {'name': 'forge', 'version': '0.1'}})
        if not isinstance(initialized, dict) or 'protocolVersion' not in initialized:
            raise ValueError('invalid initialize result')
        rpc.send({'method': 'notifications/initialized'})
        listing = rpc.call('tools/list', {})
    finally:
        rpc.close()
    if not isinstance(listing, dict) or not isinstance(listing.get('tools'), list) or listing.get('nextCursor'):
        raise ValueError('invalid or paginated tools/list; the contract needs the complete inventory in one page')
    tools = listing['tools']
    names = []
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get('name'), str) or not isinstance(tool.get('inputSchema'), dict):
            raise TypeError('invalid tool definition in tools/list')
        if not TOOL_NAME.fullmatch(tool['name']):
            raise ValueError(f"tool name {tool['name']!r} is outside the contract charset ^[a-z0-9][a-z0-9_]*$; "
                             'thClaws would sanitize it silently, the builder rejects it')
        names.append(tool['name'])
    if not names or len(names) != len(set(names)):
        raise ValueError(f'{server}: tools/list must advertise a non-empty set of unique tools')
    return sorted(tools, key=lambda tool: tool['name'])


def proposed_mutating(tool: dict) -> tuple[bool, str]:
    """A proposal from MCP annotations only; thClaws ignores annotations, so the author must confirm."""
    annotations = tool.get('annotations')
    if not isinstance(annotations, dict):
        return False, 'no annotations advertised; decide whether this tool has side effects'
    if annotations.get('destructiveHint') is True or annotations.get('readOnlyHint') is False:
        return True, 'proposed mutating from annotations (readOnlyHint false or destructiveHint true); confirm'
    if annotations.get('readOnlyHint') is True:
        return False, 'annotations claim read-only; the server must still reject mutation at tools/call'
    return False, 'annotations do not state read-only or destructive; decide whether this tool has side effects'


def yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def yaml_list(values: list[str]) -> str:
    return '[' + ', '.join(yaml_scalar(v) for v in values) + ']'


def discover(server: str, command: str, args: list[str], env_names: list[str], *, timeout: float = 10) -> str:
    """Draft a v2 pack.yaml from what the MCP server advertises. Deterministic for the same server output."""
    if not SERVER_NAME.fullmatch(server):
        raise ValueError('server name must match ^[a-z0-9][a-z0-9-]*$ (it becomes the tool prefix)')
    if not isinstance(command, str) or not command or any(not isinstance(a, str) for a in args):
        raise ValueError('command and args must be strings')
    env = clean_env(env_names)
    tools = tool_inventory(server, command, args, env, timeout)
    surface = digest([{'name': t['name'], 'inputSchema': t['inputSchema']} for t in tools])
    mutating = [t['name'] for t in tools if proposed_mutating(t)[0]]
    lines = [
        f'# Draft by `forge pack discover` for server {yaml_scalar(server)}; NOT conformant until every TODO is',
        '# replaced and `forge pack test` passes. Tier, network, env, mutating and fixture are the author\'s decisions.',
        f'# tools/list digest: {surface}',
        f'min_tier: {TODO}          # T0 | T1 | T2' + (' — proposed mutating tools above require T2' if mutating else ''),
        f'network: {TODO}           # none | allowlist | general-http',
        'hosts: []               # non-empty only when network is allowlist',
        f'env: {yaml_list(sorted(env_names))}' + ('' if env_names else '                 # env NAMES only; values come from the operator'),
        'tools: []               # built-in thClaws tools the pack also needs',
        'mcp_servers:',
        f'  - name: {yaml_scalar(server)}',
        f'    command: {yaml_scalar(command)}',
        f'    args: {yaml_list(args)}' + ('' if any(a.startswith('scripts/') for a in args)
                                           else '   # relocate the server under scripts/ and reference scripts/<file>.py'),
        f'    tools: {yaml_list([t["name"] for t in tools])}',
        f'    mutating: {yaml_list(mutating)}' + '   # proposal from annotations only; confirm or edit',
        'scripts: []             # list scripts/<file>.py assets the server needs',
        'skills: []',
        'fixture:',
        f'  setup: fixture/setup.py   # {TODO}: synthetic data, prints the declared env values as JSON',
        f'  cases: []                 # {TODO}: at least one expect: ok and one expect: error per server',
        '# Advertised tools (reference only; thClaws registers every one of them and ignores annotations):',
    ]
    for tool in tools:
        _, note = proposed_mutating(tool)
        description = tool.get('description') if isinstance(tool.get('description'), str) else ''
        lines.append(f'# - {tool["name"]}: {" ".join(description.split())[:200]}')
        lines.append(f'#   inputSchema: {json.dumps(tool["inputSchema"], sort_keys=True, separators=(",", ":"))}')
        lines.append(f'#   {note}')
    return '\n'.join(lines) + '\n'
