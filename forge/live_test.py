"""Isolated /agent/run golden-case audit. Only deterministic evidence promotes candidate."""
from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from jsonschema import Draft202012Validator

from forge.audit import audit_package, exit_code, write_report
from forge.generate import ROOT
from forge.pack_test import FixtureUnavailable, setup_fixture
from forge.packs import mcp_tools, read_pack
from forge.spec import ATLAS_TARGETS


def json_object(text):
    def invalid(value):
        raise ValueError('non-finite JSON number')

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result

    return json.loads(text, parse_constant=invalid, object_pairs_hook=unique)


def parse_sse(lines):
    """Parse native thClaws SSE, including multi-line data and its terminal sentinel."""
    events, data, name = [], [], 'message'
    total = 0
    done = False
    for line in lines:
        total += len(line)
        if total > 4 * 1024 * 1024:
            raise ValueError('SSE exceeds 4 MiB')
        line = line.decode('utf-8').rstrip('\r\n')
        if not line:
            if data:
                payload = '\n'.join(data)
                if payload == '[DONE]':
                    done = True
                    break
                value = json_object(payload)
                if not isinstance(value, dict):
                    raise ValueError('SSE data must be an object')
                events.append({'event': name, 'data': value})
            name, data = 'message', []
        elif not line.startswith(':'):
            field, _, value = line.partition(':')
            value = value.removeprefix(' ')
            if field == 'event':
                name = value
            elif field == 'data':
                data.append(value)
    if not done or sum(e['event'] == 'result' for e in events) != 1:
        raise ValueError('SSE incomplete: result and [DONE] required')
    if any(e['event'] in ('error', 'tool_use_denied') for e in events):
        raise ValueError('SSE reported an error or denied tool')
    return events


def validate_case(spec, case, events):
    text = ''
    for event in events:
        if event['event'] in ('tool_use_start', 'skill_invoked'):
            text = ''  # Text before a tool call is an intermediate assistant turn.
        elif event['event'] == 'text':
            delta = event['data'].get('delta')
            if not isinstance(delta, str):
                raise ValueError('invalid text delta')
            text += delta
    value = json_object(text)
    schemas = {'result': spec['outputs'][0]['schema'], 'refusal': spec['refusal']['schema']}
    expected = case['expect']
    if not Draft202012Validator(schemas[expected]).is_valid(value):
        raise ValueError(f'output does not match expected {expected} schema')
    if Draft202012Validator(schemas['refusal' if expected == 'result' else 'result']).is_valid(value):
        raise ValueError('ambiguous output matches both result and refusal schemas')
    return value


def request(port, token, path, timeout, body=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    deadline = time.monotonic() + timeout
    timer = None
    try:
        connection.request('GET' if body is None else 'POST', path,
                           body=None if body is None else json.dumps(body),
                           headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        sock = connection.sock
        def expire():
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(max(0, deadline - time.monotonic()), expire)
        timer.daemon = True
        timer.start()
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f'{path}: HTTP {response.status} (body omitted)')
        if body is None:
            return json_object(response.read(1024 * 1024).decode())
        if response.getheader('Content-Type', '').split(';')[0] != 'text/event-stream':
            raise ValueError('/agent/run did not return SSE')

        def lines():
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('case deadline exceeded')
                if connection.sock:
                    connection.sock.settimeout(remaining)
                line = response.readline(1024 * 1024 + 1)
                if not line:
                    break
                yield line

        return parse_sse(lines())
    finally:
        if timer is not None:
            timer.cancel()
        connection.close()


def redact(value, values):
    if isinstance(value, str):
        for secret in sorted(set(values), key=len, reverse=True):
            if secret:
                value = value.replace(secret, '<redacted>')
        return value
    if isinstance(value, list):
        return [redact(v, values) for v in value]
    if isinstance(value, dict):
        return {redact(k, values): redact(v, values) for k, v in value.items()}
    return value


def run_daemon(package, spec, packs_dir, evidence, timeout, key_env):
    binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
    with tempfile.TemporaryDirectory(prefix='forge-live-') as temporary:
        base = Path(temporary).resolve()
        work = base / 'package'
        shutil.copytree(package, work, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        home = base / 'home'
        home.mkdir()
        settings = base / 'settings.json'
        settings.write_text('{}')
        token = secrets.token_urlsafe(32)
        env = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(home),
               'XDG_CONFIG_HOME': str(home / '.config'), 'THCLAWS_CONFIG': str(settings),
               'THCLAWS_MCP_ALLOW_ALL': '1', 'THCLAWS_API_TOKEN': token,
               key_env: os.environ[key_env]}
        hidden = [token, os.environ[key_env], str(base)]
        if spec['model']['id'].startswith('oai/'):
            endpoint = os.environ.get('OPENAI_COMPAT_BASE_URL', '')
            if not endpoint:
                raise FixtureUnavailable('OPENAI_COMPAT_BASE_URL is required for an oai/ model')
            from urllib.parse import urlsplit
            parsed = urlsplit(endpoint)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('invalid OpenAI-compatible endpoint; credentials must use the key env')
            if key_env != 'OPENAI_COMPAT_API_KEY':
                raise ValueError('oai/ models require provider-key-env OPENAI_COMPAT_API_KEY')
            env['OPENAI_COMPAT_BASE_URL'] = endpoint
            hidden.append(endpoint)
        expected_servers, required_tools = set(), {}
        for capability in spec['capabilities']:
            name = capability['pack']
            folder = packs_dir / name
            pack = read_pack(folder)
            fixture = base / ('fixture-' + name)
            fixture.mkdir()
            values, provenance = setup_fixture(folder, pack, fixture, min(timeout, 30))
            if provenance:
                evidence['provenance'][name] = provenance
                if not provenance['matched']:
                    raise ValueError('SQL source revision differs from source.ref')
            if set(values) & set(env):
                raise ValueError('fixture env collides with daemon isolation or another pack')
            env.update(values)
            hidden.extend(values.values())
            expected_servers.update(s['name'] for s in pack['mcp_servers'])
            if pack['mcp_servers']:
                required_tools[name] = mcp_tools(pack)
        if set(spec['env']) - set(env):
            raise FixtureUnavailable('no fixture values for all declared env names')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen([binary, '--serve', '--port', str(port)], cwd=work, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.monotonic() + min(timeout, 30)
            while True:
                if process.poll() is not None:
                    raise ValueError('temporary daemon exited before readiness')
                try:
                    request(port, token, '/v1/agent/info', 1)
                    break
                except (OSError, ValueError, http.client.HTTPException):
                    if time.monotonic() >= deadline:
                        raise ValueError('temporary daemon readiness timed out') from None
                    time.sleep(0.1)
            called = set()
            for case in spec['evaluation']['golden_cases']:
                result = {'id': case['id'], 'expect': case['expect'], 'status': 'failed', 'events': [], 'detail': ''}
                evidence['cases'].append(result)
                try:
                    events = request(port, token, '/agent/run', timeout,
                                     {'prompt': json.dumps(case['input']), 'workspace_dir': str(work),
                                      'model': spec['model']['id'], 'stream': True})
                    result['events'] = redact(events, hidden)
                    validate_case(spec, case, events)
                    starts = {(e['data'].get('id'), e['data'].get('name')) for e in events
                              if e['event'] == 'tool_use_start'}
                    called.update(e['data']['name'] for e in events if e['event'] == 'tool_use_result'
                                  and e['data'].get('status') == 'ok'
                                  and (e['data'].get('id'), e['data'].get('name')) in starts)
                    result['status'] = 'passed'
                except (ValueError, OSError, http.client.HTTPException) as exc:
                    result['detail'] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            info = request(port, token, '/v1/agent/info', min(timeout, 10))
            servers = info.get('mcp_servers', [])
            evidence['daemon_servers'] = [{'name': s.get('name'), 'tool_count': s.get('tool_count')}
                                          for s in servers if isinstance(s, dict)]
            observed = {s['name'] for s in evidence['daemon_servers']}
            evidence['called_tools'] = sorted(called)
            if not expected_servers <= observed:
                raise ValueError('/v1/agent/info is missing a declared MCP server')
            for name, tools in required_tools.items():
                if not tools & called:
                    raise ValueError(f'no successful paired SSE tool event for pack {name}')
            if any(c['status'] != 'passed' for c in evidence['cases']):
                raise ValueError('one or more golden cases failed')
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)


def live_audit(package: Path, *, packs_dir: Path | None = None, timeout: float = 120,
               key_env: str = 'OPENAI_API_KEY', strict: bool = False):
    package = Path(package).resolve()
    if not re.fullmatch(r'[A-Z][A-Z0-9_]*_API_KEY', key_env):
        raise ValueError('provider-key-env must be a provider *_API_KEY env name')
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be finite and positive')
    report = audit_package(package, packs_dir=packs_dir, strict=strict)
    evidence = {'status': 'skipped', 'detail': '', 'cases': [], 'daemon_servers': [],
                'called_tools': [], 'provenance': {}}
    report['live_audit'] = evidence
    code = exit_code(report)
    try:
        if code:
            if code == 1:
                raise ValueError('static or manifest audit failed; live audit blocked')
            raise FixtureUnavailable('thclaws unavailable; package remains unverified')
        spec = json.loads((package / 'agentspec.json').read_text())
        if spec['target'] not in ATLAS_TARGETS:
            raise ValueError('M6 supports /agent/run only; standalone requires M9')
        if len(spec['outputs']) != 1 or spec['outputs'][0]['transport'] != 'assistant_json':
            raise ValueError('M6 requires one sole assistant_json output')
        if spec['model']['mode'] != 'pinned':
            raise ValueError('live audit requires a pinned model')
        if not os.environ.get(key_env, '').strip():
            raise FixtureUnavailable(f'no provider key in {key_env}; package remains draft')
        run_daemon(package, spec, packs_dir or ROOT / 'packs', evidence, timeout, key_env)
        evidence['status'] = 'passed'
        report['package_status'] = 'candidate'
        for row in report['guarantee_matrix']:
            if row['claim'] == 'output_schema':
                row.update(status='Enforced', basis='Golden responses validated against expected and opposite branches during live audit only.')
            elif row['claim'] == 'skill_mcp_calls' and evidence['called_tools']:
                row.update(status='Evidence', basis='Successful paired SSE tool events recorded in live_audit.called_tools.')
        code = 0
    except FixtureUnavailable as exc:
        evidence['detail'], code = str(exc), 2
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError, http.client.HTTPException) as exc:
        evidence.update(status='failed', detail=str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
        code = 1
    report['audit']['live'] = evidence['status']
    return report, code


def configure(parser):
    parser.add_argument('package', type=Path)
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--strict', action='store_true')
    parser.add_argument('--timeout', type=float, default=120, help='seconds per case (default: 120)')
    parser.add_argument('--provider-key-env', default='OPENAI_API_KEY', help='env name for the pinned model provider')


def command(args):
    try:
        report, code = live_audit(args.package, timeout=args.timeout, key_env=args.provider_key_env, strict=args.strict)
        if args.write:
            write_report(args.package, report)
        print(f"live audit {['PASS', 'FAIL', 'SKIP'][code]}: {report['live_audit']['detail']}")
        print(f"package_status={report['package_status']}")
        return code
    except (ValueError, OSError) as exc:
        print(f'live audit FAIL: {exc}')
        return 1


def main():
    parser = argparse.ArgumentParser(prog='forge live-test')
    configure(parser)
    return command(parser.parse_args())


if __name__ == '__main__':
    raise SystemExit(main())
