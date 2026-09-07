"""Bounded stdlib stdio MCP conformance harness; fixtures never need a provider key."""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, SchemaError

from forge.packs import asset, digest, pack_digest, read_pack, server_config


class RPC:
    def __init__(self, argv, cwd, env, timeout):
        self.process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        start_new_session=True)
        self.timeout = timeout
        self.messages = queue.Queue(maxsize=256)
        self.counter = 0
        self.closed = threading.Event()
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self):
        try:
            while not self.closed.is_set():
                line = self.process.stdout.readline(1024 * 1024 + 1)
                if not line:
                    raise ValueError('MCP exited before replying')
                if len(line) > 1024 * 1024:
                    raise ValueError('MCP response exceeds 1 MiB')
                self.messages.put_nowait(json.loads(line))
        except (ValueError, OSError, queue.Full) as exc:
            try:
                self.messages.put_nowait(exc)
            except queue.Full:
                pass

    def send(self, message):
        self.process.stdin.write((json.dumps({'jsonrpc': '2.0', **message}) + '\n').encode())
        self.process.stdin.flush()

    def call(self, method, params):
        self.counter += 1
        self.send({'id': self.counter, 'method': method, 'params': params})
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                message = self.messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                raise ValueError(f'MCP timeout during {method}') from None
            if isinstance(message, Exception):
                raise message
            if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
                raise ValueError('malformed JSON-RPC response')
            if 'method' in message:
                if 'id' in message:
                    self.send({'id': message['id'], 'error': {'code': -32601, 'message': 'unsupported'}})
                continue
            if message.get('id') != self.counter or 'error' in message or 'result' not in message:
                raise ValueError(f'invalid or error MCP response during {method}')
            return message['result']

    def close(self):
        import signal
        self.closed.set()
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.process.wait(timeout=5)
        for pipe in (self.process.stdin, self.process.stdout):
            pipe.close()
        self.reader.join(timeout=1)


class FixtureUnavailable(ValueError):
    pass


def setup_fixture(folder: Path, pack: dict, work: Path, timeout: float) -> tuple[dict, dict]:
    """Run the fixture without user secrets; return env separately from safe provenance evidence."""
    env = {'PATH': os.environ.get('PATH', os.defpath)}
    setup = subprocess.run([sys.executable, str(asset(folder, pack['fixture']['setup']).resolve()), str(work)],
                           cwd=work, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    if setup.returncode == 2:
        raise FixtureUnavailable('fixture runtime unavailable (setup exit 2)')
    if setup.returncode:
        raise ValueError('fixture setup failed (output omitted to avoid leaking env values)')
    values = json.loads(setup.stdout)
    if (not isinstance(values, dict) or set(values) != set(pack['env'])
            or any(not isinstance(v, str) for v in values.values()) or 'PATH' in values):
        raise ValueError('fixture must emit exactly the declared env names (PATH reserved)')
    provenance = {}
    if 'SQL_READONLY_AI_ROOT' in values:
        result = subprocess.run(['git', '-C', values['SQL_READONLY_AI_ROOT'], 'rev-parse', 'HEAD'],
                                env=env, capture_output=True, text=True, timeout=timeout, check=False)
        revision = result.stdout.strip()
        provenance = {'repo': pack['source']['repo'], 'expected_ref': pack['source']['ref'],
                      'actual_ref': revision, 'matched': result.returncode == 0 and revision == pack['source']['ref']}
    return values, provenance


def test_pack(name: str, packs_dir: Path, *, timeout: float = 10) -> tuple[dict, int]:
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', name):
        raise ValueError('invalid pack name')
    folder = packs_dir.resolve() / name
    report = {'status': 'failed', 'pack_digest': None, 'servers': {}, 'cases': [], 'detail': ''}
    code = 1
    try:
        pack = read_pack(folder)
        report['pack_digest'] = pack_digest(pack, folder)
        old_path = folder / 'pack-conformance.json'
        previous = json.loads(asset(folder, 'pack-conformance.json').read_text()) if old_path.exists() else {}
        if not isinstance(previous, dict) or not isinstance(previous.get('servers', {}), dict):
            raise TypeError('invalid previous conformance evidence')
        with tempfile.TemporaryDirectory(prefix='forge-pack-') as temp:
            work = Path(temp)
            # Match generated package paths, not paths specific to this checkout.
            for script in pack['scripts']:
                target = work / '.thclaws/scripts' / f'{name}--{script}'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(asset(folder, 'scripts/' + script).read_bytes())
            env = {'PATH': os.environ.get('PATH', os.defpath)}
            try:
                values, provenance = setup_fixture(folder, pack, work, timeout)
            except FixtureUnavailable as exc:
                report.update(status='skipped', detail=str(exc))
                code = 2
            else:
                if provenance:
                    report['provenance'] = provenance
                    if not provenance['matched']:
                        raise ValueError('SQL source revision differs from source.ref')
                env.update(values)
                configs = server_config(name, pack)
                for server in pack['mcp_servers']:
                    config = configs[server['name']]
                    if shutil.which(config['command'], path=env['PATH']) is None:
                        report.update(status='skipped', detail=f"runtime not found: {config['command']}")
                        code = 2
                        break
                    rpc = RPC([config['command'], *config['args']], work, env, timeout)
                    try:
                        initialized = rpc.call('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                                             'clientInfo': {'name': 'forge', 'version': '0.1'}})
                        if not isinstance(initialized, dict) or 'protocolVersion' not in initialized:
                            raise ValueError('invalid initialize result')
                        rpc.send({'method': 'notifications/initialized'})
                        listing = rpc.call('tools/list', {})
                        if not isinstance(listing, dict):
                            raise TypeError('invalid tools/list result')
                        tools = listing.get('tools', [])
                        if listing.get('nextCursor') or not isinstance(tools, list):
                            raise ValueError('invalid or paginated tools/list; complete inventory required')
                        if any(not isinstance(t, dict) or not isinstance(t.get('name'), str)
                               or not isinstance(t.get('inputSchema'), dict) for t in tools):
                            raise ValueError('invalid tool definition')
                        names = [t['name'] for t in tools]
                        if len(names) != len(set(names)) or set(names) != set(server['tools']):
                            raise ValueError(f"{server['name']}: tools/list differs from declared tools")
                        for tool in tools:
                            Draft202012Validator.check_schema(tool['inputSchema'])
                            if (tool['name'] in server['mutating']
                                    and not {'dry_run', 'idempotency_key'} <= set(tool['inputSchema'].get('properties', {}))):
                                raise ValueError('mutating tool requires dry_run and idempotency_key inputs')
                        surface = digest(sorted([{'name': t['name'], 'inputSchema': t['inputSchema']} for t in tools],
                                                key=lambda t: t['name']))
                        old = previous.get('servers', {}).get(server['name'], {})
                        if not isinstance(old, dict):
                            raise TypeError('invalid previous server evidence')
                        if (previous.get('pack_digest') == report['pack_digest'] and old
                                and old.get('tools_digest') != surface):
                            report['servers'][server['name']] = old
                            raise ValueError('tools/list surface changed; review and update pack.yaml before retesting')
                        report['servers'][server['name']] = {'tools_digest': surface, 'tools': sorted(names)}
                        for index, case in enumerate(pack['fixture']['cases']):
                            prefix, tool = case['tool'].split('__')
                            if prefix != server['name']:
                                continue
                            result = rpc.call('tools/call', {'name': tool, 'arguments': case['args']})
                            if (not isinstance(result, dict) or not isinstance(result.get('content'), list)
                                    or ('isError' in result and not isinstance(result['isError'], bool))):
                                raise ValueError('invalid tools/call result')
                            actual = 'error' if result.get('isError', False) else 'ok'
                            passed = actual == case['expect']
                            report['cases'].append({'index': index, 'tool': case['tool'], 'expect': case['expect'],
                                                    'actual': actual, 'passed': passed})
                            if not passed:
                                raise ValueError(f"fixture case {index} ({case['tool']}) expected {case['expect']}, got {actual}")
                    finally:
                        rpc.close()
                else:
                    report['status'] = 'conformant'
                    code = 0
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError, yaml.YAMLError, SchemaError) as exc:
        # Do not serialize server output, fixture values, or tool result content.
        report['detail'] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
    if not folder.is_dir() or folder.is_symlink() or (folder / 'pack-conformance.json').is_symlink():
        return report, code
    (folder / 'pack-conformance.json').write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
    return report, code
