"""Pack v2 contract and deterministic asset mapping; no server execution here."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

FIELDS = {'min_tier', 'network', 'hosts', 'env', 'tools', 'mcp_servers', 'scripts', 'skills', 'fixture'}
NETWORK = ['none', 'allowlist', 'general-http']


def strings(value, label, pattern=None):
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
        raise ValueError(f'{label}: expected a list of non-empty strings')
    if len(value) != len(set(value)) or (pattern and any(not re.fullmatch(pattern, v) for v in value)):
        raise ValueError(f'{label}: duplicate or invalid name')


def asset(folder: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError(f'invalid asset path: {relative}')
    source = folder / path
    if any(p.is_symlink() for p in [source, *source.parents]):
        raise ValueError(f'asset must not use symlinks: {relative}')
    if not source.is_file():
        raise ValueError(f'missing asset: {relative}')
    return source


def validate_pack(pack, folder: Path) -> None:
    if not isinstance(pack, dict) or not FIELDS <= set(pack) or set(pack) - FIELDS - {'source'}:
        raise ValueError(f'pack v2 expected fields {sorted(FIELDS)} (source optional)')
    if pack['min_tier'] not in ('T0', 'T1', 'T2') or pack['network'] not in NETWORK:
        raise ValueError('invalid min_tier or network')
    for key, pattern in [('env', r'[A-Z][A-Z0-9_]*'), ('tools', r'[A-Za-z0-9_-]+'),
                         ('hosts', r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?'),
                         ('scripts', r'[a-z0-9][a-z0-9_-]*\.py'), ('skills', r'[a-z0-9][a-z0-9_-]*')]:
        strings(pack[key], key, pattern)
    if any('__' in tool for tool in pack['tools']):
        raise ValueError('pack.tools must contain built-ins only; declare MCP tools on their server')
    if (pack['network'] == 'allowlist' and not pack['hosts']) or (pack['network'] == 'none' and pack['hosts']):
        raise ValueError('hosts must match network requirement')
    if not isinstance(pack['mcp_servers'], list):
        raise TypeError('mcp_servers must be a list')
    names = []
    for server in pack['mcp_servers']:
        if not isinstance(server, dict) or set(server) != {'name', 'command', 'args', 'tools', 'mutating'}:
            raise ValueError('server requires name/command/args/tools/mutating only (no secret env values)')
        strings([server['name']], 'server name', r'[a-z0-9][a-z0-9-]*')
        strings([server['command']], 'command')
        # Arguments may repeat; they are literal argv, never shell text or templates.
        if not isinstance(server['args'], list) or any(not isinstance(v, str) for v in server['args']):
            raise ValueError('args must be strings')
        strings(server['tools'], 'server tools', r'[a-z0-9][a-z0-9_]*')
        strings(server['mutating'], 'mutating tools')
        if not server['tools'] or any('__' in t for t in server['tools']):
            raise ValueError('server tools must be nonempty bare names without __')
        if not set(server['mutating']) <= set(server['tools']):
            raise ValueError('mutating must be a subset of server tools')
        if server['mutating'] and pack['min_tier'] != 'T2':
            raise ValueError('mutating tools require min_tier T2')
        names.append(server['name'])
    strings(names, 'server names')
    fixture = pack['fixture']
    if not isinstance(fixture, dict) or set(fixture) != {'setup', 'cases'}:
        raise ValueError('fixture requires setup and cases')
    if not isinstance(fixture['setup'], str):
        raise TypeError('fixture.setup must be a path')
    asset(folder, fixture['setup'])
    if not isinstance(fixture['cases'], list):
        raise TypeError('fixture.cases must be a list')
    qualified = mcp_tools(pack)
    for case in fixture['cases']:
        if (not isinstance(case, dict) or set(case) != {'tool', 'args', 'expect'}
                or case['tool'] not in qualified or not isinstance(case['args'], dict)
                or case['expect'] not in ('ok', 'error')):
            raise ValueError('invalid fixture case: expected qualified tool, args object, expect ok/error')
    for server in pack['mcp_servers']:
        cases = [c for c in fixture['cases'] if c['tool'].startswith(server['name'] + '__')]
        if not any(c['expect'] == 'ok' for c in cases) or not any(c['expect'] == 'error' for c in cases):
            raise ValueError('each server requires smoke and negative fixture cases')
    for name in pack['scripts']:
        asset(folder, 'scripts/' + name)
    for name in pack['skills']:
        asset(folder, 'skills/' + name + '/SKILL.md')
    if 'source' in pack:
        source = pack['source']
        if not isinstance(source, dict) or set(source) != {'repo', 'ref'}:
            raise ValueError('source requires repo and ref')
        strings([source['repo'], source['ref']], 'source')


def read_pack(folder: Path) -> dict:
    pack = yaml.safe_load(asset(folder, 'pack.yaml').read_text(encoding='utf-8'))
    validate_pack(pack, folder)
    return pack


def mcp_tools(pack: dict) -> set[str]:
    return {s['name'] + '__' + t for s in pack['mcp_servers'] for t in s['tools']}


def server_config(name: str, pack: dict) -> dict:
    # Explicit script arguments relocate with the asset; other arguments stay literal.
    mapping = {'scripts/' + s: f'.thclaws/scripts/{name}--{s}' for s in pack['scripts']}
    return {s['name']: {'command': s['command'], 'args': [mapping.get(a, a) for a in s['args']]}
            for s in pack['mcp_servers']}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def pack_digest(pack: dict, folder: Path) -> str:
    paths = [pack['fixture']['setup'], *['scripts/' + s for s in pack['scripts']],
             *['skills/' + s + '/SKILL.md' for s in pack['skills']]]
    return digest({'pack': pack, 'assets': {p: hashlib.sha256(asset(folder, p).read_bytes()).hexdigest()
                                          for p in sorted(paths)}})


def conformance(folder: Path, pack: dict) -> dict | None:
    path = folder / 'pack-conformance.json'
    if not path.exists():
        return None
    report = json.loads(asset(folder, 'pack-conformance.json').read_text())
    if not isinstance(report, dict):
        raise TypeError('invalid pack-conformance.json')
    if report.get('status') != 'conformant' or report.get('pack_digest') != pack_digest(pack, folder):
        raise ValueError('pack conformance failed, skipped or stale; rerun forge pack test')
    if 'SQL_READONLY_AI_ROOT' in pack['env']:
        proof = report.get('provenance', {})
        if (not isinstance(proof, dict) or proof.get('matched') is not True
                or proof.get('actual_ref') != pack['source']['ref']
                or proof.get('expected_ref') != pack['source']['ref']
                or proof.get('repo') != pack['source']['repo']):
            raise ValueError('missing or mismatched SQL source provenance; rerun forge pack test')
    expected_cases = [{'index': i, 'tool': c['tool'], 'expect': c['expect'], 'actual': c['expect'], 'passed': True}
                      for i, c in enumerate(pack['fixture']['cases'])]
    if sorted(report.get('cases', []), key=lambda c: c['index']) != expected_cases:
        raise ValueError('incomplete conformance case evidence; rerun forge pack test')
    servers = report.get('servers')
    if not isinstance(servers, dict) or set(servers) != {s['name'] for s in pack['mcp_servers']}:
        raise ValueError('incomplete conformance server evidence')
    for server in pack['mcp_servers']:
        evidence = servers[server['name']]
        if (not isinstance(evidence, dict) or evidence.get('tools') != sorted(server['tools'])
                or not re.fullmatch(r'[0-9a-f]{64}', str(evidence.get('tools_digest')))):
            raise ValueError('invalid tools/list conformance evidence')
    return report
