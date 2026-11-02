#!/usr/bin/env python3
"""Rehearse M18a runbook §2–§11 with sqlite-reader on a dev machine.

Exit 0 = all steps passed; 1 = failure after cleanup; 2 = explicit missing prerequisite.
--self-check exercises pure logic offline in temporary directories, without thClaws or TCP.
This is rehearsal evidence, never real-host deployment evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import plistlib
import re
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import islice
from pathlib import Path, PurePosixPath
from typing import NamedTuple
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.dont_write_bytecode = True

from jsonschema import Draft202012Validator
from render_launcher import DAEMON_ENV, FIXED_ENV, context, render

from forge.atlas_export import ATLAS_REF, export
from forge.audit import audit_package, runtime_identity, write_report
from forge.generate import STANDALONE_THCLAWS_COMPATIBILITY, THCLAWS_BASELINE, generate
from forge.live_test import check_layout, redact, request
from forge.pack_test import FixtureUnavailable, setup_fixture, test_pack
from forge.packs import read_pack
from forge.spec import load_spec

SPEC_NAME = 'spec.yaml'
WORKERS_PATH = '/api/workers'
OPERATOR_STEP = 'operator step'
DEFAULT_MODEL = 'oai/gpt-5.4-mini'
DEFAULT_BASE_URL = 'http://127.0.0.1:4319'
MISSING_THCLAWS = '/nonexistent/thclaws'
OPENAI_COMPAT_PROVIDER = ('OPENAI_COMPAT_API_KEY', 'OPENAI_COMPAT_BASE_URL')
OPENAI_PROVIDER = ('OPENAI_API_KEY', None)
REHEARSAL_ROOT = ROOT / 'out' / 'm18a-rehearsal'
FIXTURE = ROOT / 'fixtures' / 'sqlite-reader' / SPEC_NAME
PACK = ROOT / 'packs' / 'sqlite-readonly'
FORBIDDEN_ENV = {'THCLAWS_PROJECT_ROOT', 'THCLAWS_OFFICE_APPS'}
OUT_ROOT = os.path.realpath(ROOT / 'out')
SECRET_VALUES = []
TERMINAL_STATES = {'succeeded', 'failed', 'cancelled', 'paused', 'waiting_for_human', 'recovery_required'}


class Failure(ValueError):
    pass


class Skip(Exception):
    pass


STEP_ERRORS = (Skip, FixtureUnavailable, AssertionError, ValueError, OSError, KeyError, TypeError,
               subprocess.SubprocessError, tarfile.TarError, http.client.HTTPException, ImportError, RuntimeError)


def passed(step):
    print(f'M18a REHEARSAL PASS: {step}', flush=True)


class BaseURL(NamedTuple):
    url: str
    port: int


def base_url_value(value):
    message = ('--base-url must be an http(s) URL with a hostname, '
               'no credentials/query/fragment and port 1–65535')
    try:
        parts = urlsplit(value)
        if (parts.scheme not in ('http', 'https') or not parts.hostname
                or parts.username is not None or parts.password is not None or '?' in value or '#' in value):
            raise ValueError(message)
        port = parts.port if parts.port is not None else {'http': 80, 'https': 443}[parts.scheme]
        if not 1 <= port <= 65535:
            raise ValueError(message)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(message) from exc
    return BaseURL(value, port)


def out_name(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', value):
        raise argparse.ArgumentTypeError('--out must be a single folder name of 1–64 letters/digits/._-')
    return value


def launchd_package_root(value):
    path = PurePosixPath(value)
    if not path.is_absolute() or '..' in path.parts or '\0' in value:
        raise Failure('--package-root must be an absolute POSIX path without ..')
    return value


def choose_out(base, day):
    candidate, suffix = base / day, 1
    while candidate.exists() or candidate.is_symlink():
        suffix += 1
        candidate = base / f'{day}-{suffix}'
    return candidate


def inside_out(path, label):
    """Every directory the harness writes resolves under <repo>/out/ (S8707: CLI paths stay contained)."""
    resolved = os.path.realpath(path)
    if not resolved.startswith(OUT_ROOT + os.sep):
        raise Failure(f'{label} must resolve inside the repo out/ directory')
    return Path(resolved)


def output_path(selected):
    path = (REHEARSAL_ROOT / selected if selected is not None
            else choose_out(REHEARSAL_ROOT, date.today().isoformat()))
    if path.exists() or path.is_symlink():
        raise Failure('--out already exists')
    return inside_out(path, '--out')


def read_dotenv(path):
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.split('=', 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        values[name.strip()] = value
    return values


def provider_names(model):
    if model.startswith('oai/'):
        return OPENAI_COMPAT_PROVIDER
    return OPENAI_PROVIDER


def provider_values(names, path):
    names = [name for name in names if name is not None]
    fallback = read_dotenv(path) if any(name not in os.environ for name in names) else {}
    values = {name: os.environ.get(name, fallback.get(name, '')) for name in names}
    for name, value in values.items():
        if not value:
            raise Skip(f'provider credential absent: {name}')
    return values


def preflight(model, port):
    os.environ.setdefault('THCLAWS_DISABLE_KEYCHAIN', '1')
    binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
    if binary is None:
        raise Skip('thclaws binary absent')
    binary = os.path.abspath(binary)
    lines, version, revision = runtime_identity(binary)
    if (not lines or lines[0] != f'thclaws {THCLAWS_BASELINE}'
            or revision not in STANDALONE_THCLAWS_COMPATIBILITY[THCLAWS_BASELINE]):
        raise Skip(f'thclaws version found: {version or "<empty>"}, revision {revision or "<missing>"}; '
                   f'requires thclaws {THCLAWS_BASELINE} revision 9f69995')
    values = provider_values(provider_names(model), ROOT / '.env')
    SECRET_VALUES.extend(values.values())
    # The host is the constant loopback address; only the port comes from --base-url.
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
    try:
        connection.connect()
    except ConnectionRefusedError:
        pass
    except OSError as exc:
        raise Skip(f'cannot confirm port {port} is free: {type(exc).__name__}') from exc
    else:
        raise Skip(f'port {port} already has a listener; '
                   'it is not ours, never stop it; pass another --base-url')
    finally:
        connection.close()
    os.environ['THCLAWS_BIN'] = binary
    passed('preflight')
    return binary, values, port


def atlas_call(base, token, method, path, body=None, timeout=30):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request_ = urllib.request.Request(base.rstrip('/') + path, method=method, headers=headers,
                                      data=None if body is None else json.dumps(body).encode('utf-8'))
    try:
        with urllib.request.urlopen(request_, timeout=timeout) as response:
            return json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        try:
            error = str(json.loads(exc.read()).get('error', ''))
        except (ValueError, AttributeError):
            error = 'non-JSON error response'
        error = redact(error, [*SECRET_VALUES, token or ''])[:200]
        raise Failure(f'{method} {path}: HTTP {exc.code} {error}') from exc


def atlas_preflight(mode, token):
    if mode == 'hermetic':
        root = Path(os.environ.get('ATLAS_ROOT') or ROOT.parent / 'atlas-control-plane-daa0f49').resolve()
        hint = 'set ATLAS_ROOT to an atlas-control-plane checkout at daa0f49'
        if not (root / 'atlas/workflows.py').is_file():
            raise Skip(f'missing {root / "atlas/workflows.py"}; {hint}')
        ref = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True,
                             text=True, check=False).stdout.strip()
        if ref != ATLAS_REF:
            raise Skip(f'Atlas ref found: {ref or "<missing>"}; {hint}')
        return root, None
    base = os.environ.get('ATLAS_URL')
    if not base:
        raise Skip('ATLAS_URL must be set for --atlas prod')
    try:
        atlas_call(base, token, 'GET', WORKERS_PATH)
    except Failure as exc:
        if str(exc).startswith(('GET /api/workers: HTTP 401 ', 'GET /api/workers: HTTP 403 ')):
            raise Skip('Atlas refused the request; set ATLAS_TOKEN') from exc
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise Skip('Atlas at ATLAS_URL not reachable') from exc
    return None, base


@contextmanager
def hermetic_atlas(root):
    sys.path.insert(0, str(root))
    from atlas.app import AtlasHttpServer, AtlasRuntime
    from atlas.config import Config

    temporary = private_temp('forge-m18a-atlas-')
    runtime, server, thread = None, None, None
    try:
        runtime = AtlasRuntime(Config(host='127.0.0.1', port=0, db_path=temporary / 'atlas.sqlite',
                                      api_token=None, request_timeout_seconds=300,
                                      secret_key=secrets.token_hex(32), enable_loopback_without_token=True,
                                      upload_dir=temporary / 'uploads'))
        server = AtlasHttpServer(('127.0.0.1', 0), runtime)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f'http://127.0.0.1:{server.server_address[1]}'
    finally:
        try:
            if server is not None:
                if thread is not None and thread.is_alive():
                    server.shutdown()
                server.server_close()
        finally:
            try:
                if runtime is not None:
                    runtime.close()
            finally:
                shutil.rmtree(temporary)
                sys.path.remove(str(root))


def collision_guard(workers, worker):
    base = worker['base_url'].rstrip('/')
    for existing in workers:
        if existing['base_url'].rstrip('/') == base and existing['id'] != worker['id']:
            raise Failure(f'Atlas worker {existing["id"]} already uses {base}; registering would overwrite it '
                          '(Atlas upserts by id or base_url); pass another --base-url')


def workflow_definition(register):
    node = register['node_template']
    return {'name': node['id'], 'graph': {'start': node['id'], 'nodes': [node], 'edges': []},
            'policy': register['policy_template']}


def write_json(path, body):
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def no_token(body, token):
    if token in json.dumps(body):
        raise Failure('Atlas response contains the daemon token')


def register_atlas(base, token, daemon_token, register, created, out):
    workers = atlas_call(base, token, 'GET', WORKERS_PATH)['workers']
    collision_guard(workers, register['worker'])
    response = atlas_call(base, token, 'POST', WORKERS_PATH, {**register['worker'], 'token': daemon_token})
    # A mismatched id must never authorize deleting somebody else's worker.
    if response['worker']['id'] != register['worker']['id']:
        raise Failure('Atlas returned a different worker id')
    created['worker'] = response['worker']['id']
    no_token(response, daemon_token)
    response = atlas_call(base, token, 'POST', '/api/workspaces', register['workspace'])
    if response['workspace']['id'] != register['workspace']['id']:
        raise Failure('Atlas returned a different workspace id')
    created['workspace'] = response['workspace']['id']
    response = atlas_call(base, token, 'POST', '/api/workflows', workflow_definition(register))
    created['workflow'] = response['workflow']['id']
    poll = atlas_call(base, token, 'POST', f'/api/workers/{created["worker"]}/poll')
    write_json(out / 'poll.json', poll)
    no_token(poll, daemon_token)
    worker = poll['worker']
    if worker.get('id') != created['worker']:
        raise Failure('Atlas poll returned a different worker id')
    if worker.get('status') != 'online':
        raise Failure(f'Atlas worker poll status: {worker.get("status")}')
    info = worker.get('agent_info') or {}
    if not info.get('agent', {}).get('mcp_servers'):
        info = atlas_call(base, token, 'GET', f'/api/workers/{created["worker"]}')['worker']['agent_info']
        no_token(info, daemon_token)
    servers = sorted(s['name'] for s in info.get('agent', {}).get('mcp_servers', []))
    if servers != register['deployment']['mcp_servers']:
        raise Failure('Atlas poll MCP inventory differs from deployment.mcp_servers')
    passed('Atlas worker, workspace and workflow registered; poll online with matching MCP inventory')


def validate_artifact(body, output):
    artifact = next((item for item in body.get('artifacts', []) if item.get('key') == output['name']), None)
    if artifact is None:
        raise Failure(f'artifact missing: {output["name"]}')
    content = artifact.get('content')
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError as exc:
            raise Failure(f'artifact {output["name"]} is not JSON') from exc
    errors = list(islice(Draft202012Validator(output['schema']).iter_errors(content), 3))
    if errors:
        raise Failure('artifact schema validation failed: ' + '; '.join(error.message for error in errors))


def run_workflow(base, token, workflow_id, package, out, timeout):
    spec = json.loads((package / 'agentspec.json').read_text(encoding='utf-8'))
    case = next((case for case in spec['evaluation']['golden_cases'] if case['id'] == 'normal'), None)
    if case is None:
        raise Failure('normal golden case missing')
    body = {}
    try:
        body = atlas_call(base, token, 'POST', '/api/workflow-runs',
                          {'workflow_definition_id': workflow_id, 'input': case['input'], 'execution_mode': 'test'})
        run_id = body['run']['id']
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Failure(f'workflow run not terminal after {timeout:g} s')
            try:
                body = atlas_call(base, token, 'GET', f'/api/workflow-runs/{run_id}', timeout=min(30, remaining))
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise Failure(f'workflow run not terminal after {timeout:g} s') from exc
                raise
            state = body['run']['state']
            if state in TERMINAL_STATES:
                break
            time.sleep(min(2, max(0, deadline - time.monotonic())))
    finally:
        write_json(out / 'atlas-run.json', body)
    if state != 'succeeded':
        errors = [str(node['error']) for node in body.get('nodes', [])
                  if node.get('state') == 'failed' and node.get('error')]
        raise Failure(redact(f'workflow run state {state}: ' + '; '.join(errors), SECRET_VALUES))
    artifacts = atlas_call(base, token, 'GET', f'/api/workflow-runs/{run_id}/artifacts')
    write_json(out / 'artifacts.json', artifacts)
    validate_artifact(artifacts, spec['outputs'][0])
    passed('Atlas normal golden case succeeded in test mode; artifact matches output schema')


def conform_pack():
    report, code = test_pack('sqlite-readonly', ROOT / 'packs')
    if code == 2:
        raise Skip('sqlite-readonly fixture runtime unavailable')
    if code != 0 or report['status'] != 'conformant':
        raise Failure('sqlite-readonly pack conformance failed')
    passed('sqlite-readonly pack conformant')


def replace_model(text, model):
    original = '\n  id: gpt-5.4\n'
    if text.count(original) != 1:
        raise Failure('fixture changed: expected exactly one model id line')
    if '\n' in model or '\r' in model:
        raise Failure('--model must be a single line')
    return text.replace(original, f'\n  id: {model}\n')


def copy_spec(source, destination, model):
    destination.write_text(replace_model(source.read_text(encoding='utf-8'), model), encoding='utf-8')
    original, copied = load_spec(source), load_spec(destination)
    expected = {**original, 'model': {**original['model'], 'id': model}}
    if copied != expected:
        raise Failure('spec copy changed fields besides model.id')
    return copied


def build_package(spec, package):
    generate(spec, package)
    report = audit_package(package)
    write_report(package, report)
    if report['package_status'] != 'draft':
        errors = [f['message'] for f in report['static_audit']['findings'] if f['severity'] == 'error']
        raise Failure('static audit did not produce draft: ' + '; '.join(errors))
    passed('generate and static audit: draft')


def live_audit(package, out, binary, provider, model):
    log = out / 'live-audit.log'
    with log.open('w', encoding='utf-8') as stream:
        result = subprocess.run(
            [sys.executable, '-m', 'forge.cli', 'audit', '.', '--live', '--security', '--write',
             '--provider-key-env', provider_names(model)[0]],
            cwd=package, env={**os.environ, **provider, 'THCLAWS_BIN': binary},
            stdout=stream, stderr=subprocess.STDOUT, check=False,
        )
    report = json.loads((package / 'builder-build-report.json').read_text(encoding='utf-8'))
    if result.returncode != 0 or report['package_status'] not in ('candidate', 'shippable'):
        raise Failure(f'live + security audit failed; see {log}')
    passed('live + security audit')


def export_package(package, out, base_url, package_root):
    register, code = export(package, out / 'export', base_url=base_url, workspace_dir=str(package_root))
    if code != 0:
        raise Failure('export did not produce an archive')
    passed('export with archive')
    return register


def private_temp(prefix):
    path = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    if path.is_relative_to(ROOT) or stat.S_IMODE(path.stat().st_mode) != 0o700:
        shutil.rmtree(path)
        raise Failure('temporary directory must be mode 0700 and outside the repo')
    return path


def render_deploy(out, binary, env_file):
    files = render(out / 'export', thclaws_bin=binary, env_file=env_file)
    deploy = out / 'deploy'
    deploy.mkdir()
    for name, text in sorted(files.items()):
        (deploy / name).write_text(text, encoding='utf-8')
    passed('render launchers')
    return files


def verify_sidecar(archive):
    expected = f'{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n'
    if archive.with_name(archive.name + '.sha256').read_text(encoding='utf-8') != expected:
        raise Failure('archive sha256 sidecar digest or name mismatch')


def extract_archive(archive, destination):
    verify_sidecar(archive)
    destination = inside_out(destination, 'archive destination')
    if destination.exists() or destination.is_symlink():
        raise Failure('archive destination already exists')
    destination.mkdir(parents=True)
    with tarfile.open(archive, mode='r:gz') as contents:
        contents.extractall(destination, filter='data')
    settings = json.loads((destination / '.thclaws/settings.json').read_text(encoding='utf-8'))
    if settings.get('workspaceVersion') != 2 or (destination / '.thclaws/bots.json').exists():
        raise Failure('archive must be workspaceVersion 2 without .thclaws/bots.json')
    passed('archive digest, extraction and single-agent layout')


def fixture_values(out, names):
    work = out / 'fixture'
    work.mkdir()
    values, _provenance = setup_fixture(PACK, read_pack(PACK), work, 30)
    if set(names) - values.keys():
        raise Failure('fixture does not supply every deployment env name')
    passed('synthetic sqlite-readonly fixture')
    return values


def example_names(text):
    return {line.strip()[:-1] for line in text.splitlines()
            if re.fullmatch(r'[A-Z_][A-Z0-9_]*=', line.strip())}


def write_env(path, names, values):
    for name in sorted(names):
        if not re.fullmatch(r'[A-Z_][A-Z0-9_]*', name) or name in FORBIDDEN_ENV:
            raise Failure(f'forbidden or invalid env name: {name}')
        if not isinstance(values.get(name), str) or not values[name] or '\0' in values[name]:
            raise Failure(f'missing or invalid env value: {name}')
    text = ''.join(f'{name}={shlex.quote(values[name])}\n' for name in sorted(names))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(text)
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise Failure('env file mode must be 0600')


def self_command(files, package_root, home):
    plist = plistlib.loads(files['thclaws.sqlite-reader.plist'].encode())
    cwd = plist['WorkingDirectory']
    if cwd != str(package_root):
        raise Failure('plist WorkingDirectory differs from package root')
    env = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(home), **plist['EnvironmentVariables']}
    if FORBIDDEN_ENV & env.keys():
        raise Failure('forbidden daemon env override in plist')
    return plist['ProgramArguments'], cwd, env


def start_daemon(files, package_root, home, out):
    # The service user's HOME stays empty: the package pins browserEnabled false (DESIGN §4 9f69995 item 13).
    argv, cwd, env = self_command(files, package_root, home)
    with (out / 'daemon.log').open('w', encoding='utf-8') as log:
        return subprocess.Popen(argv, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)


def extraction_commands(archive, package_root):
    q = lambda path: shlex.quote(str(path))
    return [
        f'sudo install -d -o thclaws -g thclaws -m 0750 {q(package_root)}',
        f'(cd {q(archive.parent)} && shasum -a 256 -c {q(archive.name + ".sha256")})',
        f'sudo -u thclaws tar -xzf {q(archive)} -C {q(package_root)}',
    ]


def operator_commands(out, archive, package_root, env_file):
    q = lambda path: shlex.quote(str(path))
    plist = '/Library/LaunchDaemons/thclaws.sqlite-reader.plist'
    commands = [
        *extraction_commands(archive, package_root),
        'sudo install -d -m 0750 -o root -g thclaws /etc/thclaws',
        f'sudo install -m 0600 -o thclaws -g thclaws {q(env_file)} /etc/thclaws/sqlite-reader.env',
        'sudo install -d -o thclaws -m 0755 /var/log/thclaws',
        f'sudo install -m 0644 -o root -g wheel {q(out / "deploy/thclaws.sqlite-reader.plist")} {plist}',
        f'sudo launchctl bootstrap system {plist}',
    ]
    print('Run these operator commands before the launchd wait expires:', flush=True)
    for command in commands:
        print(command, flush=True)


def healthz(port, timeout):
    """GET /healthz on the loopback daemon (S8703: the host is constant; only the port comes from --base-url)."""
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        connection.request('GET', '/healthz')
        return connection.getresponse().status
    finally:
        connection.close()


def wait_ready(port, timeout, process=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise Failure('temporary daemon exited before readiness; see daemon.log')
        try:
            if healthz(port, 1) == 200:
                passed('daemon /healthz')
                return
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(0.2)
    if process is None:
        raise Skip(f'launchd daemon not reachable after {timeout:g} s; run the printed sudo commands')
    raise Failure('temporary daemon not ready after 30 s; see daemon.log')


def agent_info(port, token, package_root, register, out, daemon):
    info = request(port, token, '/v1/agent/info', 10)
    if info.get('workspace_dir') != str(package_root):
        raise Failure('/v1/agent/info workspace_dir differs from package root realpath')
    servers = sorted(s['name'] for s in info.get('mcp_servers', []))
    if servers != register['deployment']['mcp_servers']:
        raise Failure('/v1/agent/info MCP inventory differs from deployment.mcp_servers')
    if daemon == 'self':
        check_layout(info, package_root)
    (out / 'agent-info.json').write_text(json.dumps(info, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    passed('/v1/agent/info saved and verified')
    return servers


def stop_daemon(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.wait(timeout=10)


def wait_stopped(port, timeout):
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Failure(f'daemon /healthz still answering or stop unconfirmed after {timeout:g} s')
        try:
            healthz(port, min(1, remaining))  # any HTTP status still proves a listener answering
        except ConnectionRefusedError:
            return
        except (OSError, http.client.HTTPException):
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Failure(f'daemon /healthz still answering or stop unconfirmed after {timeout:g} s')
        time.sleep(min(0.2, remaining))


def withdraw_atlas(base, token, kind, identifier):
    path = f'/api/{kind}s/{identifier}'
    try:
        atlas_call(base, token, 'DELETE', path)
    except Failure as exc:
        if (kind != 'worker' or not str(exc).startswith(f'DELETE {path}: HTTP 400 ')
                or 'job(s) in history' not in str(exc)):
            raise
        poll = atlas_call(base, token, 'POST', f'{path}/poll')
        if poll['worker'].get('status') != 'offline':
            raise Failure('worker retained with job history but poll is not offline') from exc
        return 'worker kept: Atlas keeps workers with job history; poll offline'
    return f'deleted {identifier}'


def restore_package(daemon, archive, package_root):
    path = PurePosixPath(package_root)
    failed = path.with_name(path.name + '.failed-' + datetime.now().strftime('%Y%m%d%H%M%S'))
    if daemon == 'launchd':
        q = lambda path: shlex.quote(str(path))
        print(f'sudo mv {q(package_root)} {q(failed)}', flush=True)
        for command in extraction_commands(archive, package_root):
            print(command, flush=True)
        return
    package_root = inside_out(package_root, '--package-root')
    failed = inside_out(failed, 'rollback destination')
    if failed.exists() or failed.is_symlink():
        raise Failure('rollback destination already exists')
    package_root.rename(failed)
    extract_archive(archive, package_root)


def rollback(args, process, port, base, token, created, archive, package_root):
    results = []

    def attempt(step, action, operator=False):
        try:
            detail = action()
            results.append({'step': step, 'ok': not operator,
                            'detail': OPERATOR_STEP if operator else (detail or 'completed')})
        except STEP_ERRORS as exc:
            results.append({'step': step, 'ok': False,
                            'detail': redact(f'{type(exc).__name__}: {exc}', SECRET_VALUES)})

    def stop():
        if args.daemon == 'self':
            stop_daemon(process)
        else:
            print('sudo launchctl bootout system/thclaws.sqlite-reader', flush=True)
            wait_stopped(port, args.timeout)

    attempt('stop daemon', stop)
    for kind in ('workflow', 'workspace', 'worker'):
        if kind in created:
            attempt(f'withdraw {kind}', lambda kind=kind: withdraw_atlas(base, token, kind, created[kind]))
    attempt('restore archive', lambda: restore_package(args.daemon, archive, package_root),
            operator=args.daemon == 'launchd')
    return results


def checklist(daemon, atlas, proof, day):
    lines = (ROOT / 'docs/deploy-runbook.md').read_text(encoding='utf-8').splitlines()
    section, items = False, []
    for line in lines:
        if line.startswith('## 10.'):
            section = True
        elif line.startswith('## 11.'):
            break
        elif section and line.startswith('- [ ] '):
            items.append(line[6:])
    if len(items) != 10:
        raise Failure('runbook §10 changed')
    ticks = [False, proof.get('archive'), proof.get('env'), daemon == 'launchd' and proof.get('plist'),
             proof.get('info'), False, proof.get('atlas'), proof.get('run'), proof.get('rollback'), False]
    reasons = {0: 'dev machine, not a separate host',
               3: ('daemon=self runs the rendered plist command without launchd' if daemon == 'self'
                   else 'installed plist missing or differs from the rendered one'),
               5: 'a dev machine cannot prove host firewall or egress (DESIGN §6)',
               9: 'operator-only; the builder and this harness never set deployment_verified'}
    if daemon == 'launchd':
        reasons[8] = 'operator step: move aside and re-extract were printed, not observed'
    result = [(f'<!-- M18a rehearsal on a dev machine: daemon={daemon} atlas={atlas} date={day}; '
               'not host evidence -->'), '', '# M18a rehearsal checklist (runbook §10)', '']
    for index, text in enumerate(items):
        if ticks[index]:
            result.append(f'- [x] {text} — rehearsal harness {day} (dev machine)')
        else:
            reason = reasons.get(index, 'step failed or not reached')
            result.append(f'- [ ] {text} — not proven by this rehearsal: {reason}')
    return '\n'.join(result) + '\n'


def secret_scan(out, values):
    needles = [value.encode('utf-8') for value in set(values) if value]
    hits = []
    for path in sorted(out.rglob('*')):
        if path.is_symlink() or not path.is_file():
            continue
        content = path.read_bytes()
        if any(value in content for value in needles):
            hits.append(path.relative_to(out).as_posix())
    if hits:
        raise Failure('secret value found in: ' + ', '.join(hits))


def failure_message(exc, values):
    return f'M18a REHEARSAL FAIL: {type(exc).__name__}: {redact(str(exc), values)}'


def must_refuse(action):
    try:
        action()
    except (argparse.ArgumentTypeError, ValueError, FileExistsError):
        return
    raise AssertionError('operation should have refused')


def rollback_failed(results):
    return any(not step['ok'] and step['detail'] != OPERATOR_STEP for step in results)


def offline_checks(work):
    checks = argument_offline_checks()
    copied = copy_spec(FIXTURE, work / SPEC_NAME, DEFAULT_MODEL)
    assert copied['model']['id'] == DEFAULT_MODEL, 'model copy'
    assert (work / SPEC_NAME).read_text() == replace_model(FIXTURE.read_text(), DEFAULT_MODEL)
    for text in ('model:\n  id: changed\n', FIXTURE.read_text() * 2):
        must_refuse(lambda text=text: replace_model(text, 'test'))
    checks += 1

    day = '2026-11-02'
    (work / day).mkdir()
    assert choose_out(work, day) == work / f'{day}-2', 'out suffix -2'
    (work / f'{day}-2').mkdir()
    assert choose_out(work, day) == work / f'{day}-3', 'out suffix -3'
    checks += 1

    dotenv = work / 'test.env'
    dotenv.write_text('\n# comment\nA="with spaces"\nB=\'literal $x=#\'\nC=plain\n', encoding='utf-8')
    assert read_dotenv(dotenv) == {'A': 'with spaces', 'B': 'literal $x=#', 'C': 'plain'}, 'dotenv parsing'
    previous = os.environ.get('A')
    try:
        os.environ['A'] = 'environment wins'
        assert provider_values(('A', None), dotenv) == {'A': 'environment wins'}, 'environment precedence'
        os.environ['A'] = ''
        try:
            provider_values(('A', None), dotenv)
        except Skip:
            pass
        else:
            raise AssertionError('empty environment must override dotenv')
    finally:
        if previous is None:
            os.environ.pop('A', None)
        else:
            os.environ['A'] = previous
    checks += 1

    values = {'SPACE': 'a b', 'QUOTES': '\'single\' and "double"', 'DOLLAR': '$HOME $(false)',
              'EQUAL': 'a=b', 'HASH': '# literal', 'NEWLINE': 'first\nsecond'}
    env_file = work / 'sqlite-reader.env'
    write_env(env_file, values.keys(), values)
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600, 'env mode'
    must_refuse(lambda: write_env(env_file, values.keys(), values))
    must_refuse(lambda: write_env(work / 'missing.env', {'MISSING'}, {}))
    must_refuse(lambda: write_env(work / 'empty.env', {'EMPTY'}, {'EMPTY': ''}))
    for name in FORBIDDEN_ENV:
        must_refuse(lambda name=name: write_env(work / 'forbidden.env', {name}, {name: 'bad'}))
    sourced = subprocess.run(['/bin/sh', '-c', 'set -a; . "$1"; set +a; env -0', 'sh', str(env_file)],
                             env={'PATH': os.defpath}, capture_output=True, check=True)
    actual = {name: value for entry in sourced.stdout.split(b'\0') if entry
              for name, value in [entry.decode().split('=', 1)]}
    assert all(actual.get(name) == value for name, value in values.items()), 'env shell round trip'
    checks += 1

    archive = work / 'sample.tar.gz'
    archive.write_bytes(b'offline digest fixture')
    sidecar = archive.with_name(archive.name + '.sha256')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    sidecar.write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    verify_sidecar(archive)
    for text in (f'{"0" * 64}  {archive.name}\n', f'{digest}  wrong-name\n'):
        sidecar.write_text(text, encoding='utf-8')
        must_refuse(lambda: verify_sidecar(archive))
    checks += 1

    # Ignore checkout conformance evidence: these checks neither run nor certify the pack.
    packs = work / 'packs'
    shutil.copytree(PACK, packs / PACK.name,
                    ignore=shutil.ignore_patterns('pack-conformance.json', '__pycache__'))
    package = work / 'package'
    generate(copied, package, packs_dir=packs)
    write_report(package, audit_package(package, packs_dir=packs))
    package_root = Path(os.path.realpath(work / 'srv/sqlite-reader'))
    register, code = export(package, work / 'export', packs_dir=packs,
                            base_url=DEFAULT_BASE_URL, workspace_dir=str(package_root))
    assert code == 2 and register['package']['archive'] is None, 'offline export must skip archive'
    assert not list((work / 'export').glob('*.tar.gz')), 'offline export produced archive'
    files = render(work / 'export', thclaws_bin=MISSING_THCLAWS, env_file=str(env_file))
    assert example_names(files['sqlite-reader.env.example']) == {*DAEMON_ENV, *register['deployment']['env']}
    checks += 1
    argv, cwd, env = self_command(files, package_root, work / 'home')
    assert argv[:2] == ['/bin/sh', '-c'], 'plist shell argv'
    assert str(env_file) in argv[2] and '--serve --bind 127.0.0.1 --port 4319' in argv[2], 'plist command'
    assert cwd == str(package_root), 'plist cwd'
    assert all(env.get(name) == value for name, value in FIXED_ENV.items()), 'plist fixed env'
    assert not FORBIDDEN_ENV & env.keys(), 'forbidden plist env'
    assert context(register, thclaws_bin=MISSING_THCLAWS, env_file=str(env_file))['port'] == 4319
    checks += 1
    checks += atlas_offline_checks(work)
    return checks


def argument_offline_checks():
    assert base_url_value(DEFAULT_BASE_URL) == BaseURL(DEFAULT_BASE_URL, 4319)
    assert base_url_value('https://h.invalid') == BaseURL('https://h.invalid', 443)
    assert base_url_value('http://h.invalid').port == 80
    assert arguments([]).base_url == BaseURL(DEFAULT_BASE_URL, 4319), 'argparse default conversion'
    for value in ('http://user:password@h.invalid', 'http://@h.invalid', 'http://h.invalid?x=1',
                  'http://h.invalid#fragment', 'ftp://h.invalid', 'http://h.invalid:0',
                  'http://h.invalid:65536', 'http:///missing-host'):
        must_refuse(lambda value=value: base_url_value(value))
    assert out_name('2026-11-02-7') == '2026-11-02-7'
    for value in ('', '.', '..', 'a/b', '../x', 'a' * 65):
        must_refuse(lambda value=value: out_name(value))
    assert provider_names(DEFAULT_MODEL) == ('OPENAI_COMPAT_API_KEY', 'OPENAI_COMPAT_BASE_URL')
    assert provider_names('gpt-5.4') == ('OPENAI_API_KEY', None)
    assert all(isinstance(names, tuple) and len(names) == 2
               for names in (provider_names(DEFAULT_MODEL), provider_names('gpt-5.4')))
    assert launchd_package_root('/srv/atlas/sqlite-reader') == '/srv/atlas/sqlite-reader'
    for value in ('relative/path', '/srv/../sqlite-reader'):
        must_refuse(lambda value=value: launchd_package_root(value))
    return 4


def atlas_offline_checks(work):
    checks = 0
    assert not rollback_failed([{'ok': True, 'detail': 'completed'}])
    assert not rollback_failed([{'ok': True, 'detail': 'completed'}, {'ok': False, 'detail': OPERATOR_STEP}])
    assert rollback_failed([{'ok': False, 'detail': 'failed'}, {'ok': False, 'detail': OPERATOR_STEP}])
    checks += 1
    day = '2026-11-02'
    proof = dict.fromkeys(('archive', 'env', 'plist', 'info', 'atlas', 'run', 'rollback'), True)

    def unticked(text):
        items = [line for line in text.splitlines() if line.startswith('- [')]
        assert len(items) == 10, 'checklist item count'
        assert 'deployment_verified: true' not in text, 'checklist claim'
        return {index for index, line in enumerate(items) if line.startswith('- [ ] ')}

    text = checklist('self', 'hermetic', proof, day)
    assert text.startswith('<!-- M18a rehearsal on a dev machine: daemon=self atlas=hermetic '), 'marker'
    assert unticked(text) == {0, 3, 5, 9}, 'self checklist'
    assert unticked(checklist('launchd', 'prod', proof, day)) == {0, 5, 9}, 'launchd checklist'
    assert unticked(checklist('self', 'hermetic', {**proof, 'run': False}, day)) == {0, 3, 5, 7, 9}
    checks += 1

    secret = 'planted-secret-for-offline-check'
    evidence = work / 'evidence'
    (evidence / 'nested').mkdir(parents=True)
    planted = evidence / 'nested/sample.txt'
    planted.write_text(secret, encoding='utf-8')
    try:
        secret_scan(evidence, [secret])
    except Failure as exc:
        assert str(exc) == 'secret value found in: nested/sample.txt', 'scan relative path'
        assert secret not in str(exc), 'scan disclosed secret'
    else:
        raise AssertionError('secret scan missed planted value')
    planted.write_text('clean tree', encoding='utf-8')
    secret_scan(evidence, [secret, ''])
    checks += 1

    worker = {'id': 'ours', 'base_url': 'https://worker.invalid:4319'}
    for base in (worker['base_url'], worker['base_url'] + '/'):
        must_refuse(lambda base=base: collision_guard([{'id': 'other', 'base_url': base}], worker))
    collision_guard([worker], worker)
    collision_guard([{'id': 'other', 'base_url': 'https://other.invalid:4319'}], worker)
    checks += 1

    assert inside_out(ROOT / 'out/m18a-rehearsal/x', 'x') == Path(OUT_ROOT) / 'm18a-rehearsal/x', 'inside out/'
    for outside in (ROOT / 'out', ROOT / 'out/../docs', work):
        must_refuse(lambda outside=outside: inside_out(outside, 'x'))
    checks += 1

    node, policy = {'id': 'single', 'worker_id': 'ours'}, {'allowed_worker_ids': ['ours']}
    definition = workflow_definition({'node_template': node, 'policy_template': policy})
    assert definition == {'name': 'single', 'graph': {'start': 'single', 'nodes': [node], 'edges': []},
                          'policy': policy}, 'single-node workflow shape'
    checks += 1

    output = {'name': 'result', 'schema': {'type': 'object', 'required': ['answer'],
                                         'properties': {'answer': {'type': 'integer'}}}}
    for content in ({'answer': 42}, '{"answer": 42}'):
        validate_artifact({'artifacts': [{'key': 'result', 'content': content}]}, output)
    for content in ({'answer': 'wrong'}, {}, 'not JSON'):
        must_refuse(lambda content=content: validate_artifact(
            {'artifacts': [{'key': 'result', 'content': content}]}, output))
    must_refuse(lambda: validate_artifact({'artifacts': [{'key': 'wrong', 'content': {}}]}, output))
    checks += 1

    message = failure_message(RuntimeError(f'provider said {secret}'), [secret])
    assert message == 'M18a REHEARSAL FAIL: RuntimeError: provider said <redacted>', 'redacted FAIL'
    assert secret not in message, 'FAIL disclosed secret'
    checks += 1
    return checks


def self_check():
    previous = os.environ.get('THCLAWS_BIN')
    os.environ['THCLAWS_BIN'] = MISSING_THCLAWS
    try:
        with tempfile.TemporaryDirectory(prefix='forge-m18a-self-check-') as temporary:
            checks = offline_checks(Path(temporary).resolve())
    except AssertionError as exc:
        print(f'M18a REHEARSAL self-check FAIL: {exc}')
        return 1
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f'M18a REHEARSAL self-check FAIL: {type(exc).__name__}')
        return 1
    finally:
        if previous is None:
            os.environ.pop('THCLAWS_BIN', None)
        else:
            os.environ['THCLAWS_BIN'] = previous
    print(f'M18a REHEARSAL self-check PASS: {checks} checks')
    return 0


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--base-url', type=base_url_value, default=DEFAULT_BASE_URL)
    parser.add_argument('--out', type=out_name,
                        help='new evidence folder name under out/m18a-rehearsal/; default first free date')
    parser.add_argument('--package-root', help='deployment path; default depends on --daemon')
    parser.add_argument('--daemon', choices=('self', 'launchd'), default='self')
    parser.add_argument('--atlas', choices=('hermetic', 'prod'), default='hermetic')
    parser.add_argument('--live', action='store_true', help='also run live + security audit')
    parser.add_argument('--timeout', type=float, default=600,
                        help='workflow run wait and launchd waits timeout in seconds')
    parser.add_argument('--self-check', action='store_true', help='offline checks; ignores other flags')
    return parser.parse_args(argv)


@dataclass
class Rehearsal:
    args: argparse.Namespace
    out: Path | None = None
    package_root: Path | str | None = None
    binary: str | None = None
    provider: dict = field(default_factory=dict)
    port: int | None = None
    atlas_root: Path | None = None
    base: str | None = None
    register: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)
    archive: Path | None = None
    env_dir: Path | None = None
    home: Path | None = None
    env_file: Path | None = None
    token: str = ''
    atlas_token: str = ''
    process: subprocess.Popen | None = None
    created: dict = field(default_factory=dict)
    proof: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    results: list = field(default_factory=list)
    atlas_stack: ExitStack = field(default_factory=ExitStack)
    started: bool = False

    def request_token(self):
        return self.atlas_token if self.args.atlas == 'prod' else ''


def prepare(state):
    args = state.args
    state.atlas_token = os.environ.get('ATLAS_TOKEN') or ''
    SECRET_VALUES.clear()
    SECRET_VALUES.extend([state.atlas_token, *(os.environ.get(name, '')
                                              for name in provider_names(args.model) if name is not None)])
    if not 0 < args.timeout < float('inf'):
        raise Failure('--timeout must be positive and finite')
    state.out = output_path(args.out)
    if args.daemon == 'self':
        state.package_root = inside_out(args.package_root or state.out / 'srv/sqlite-reader',
                                        '--package-root')
    else:
        state.package_root = launchd_package_root(args.package_root or '/srv/atlas/sqlite-reader')
    state.binary, state.provider, state.port = preflight(args.model, args.base_url.port)
    state.atlas_root, state.base = atlas_preflight(args.atlas, state.atlas_token)
    conform_pack()
    state.out.mkdir(parents=True)


def build_export(state):
    args, out = state.args, state.out
    spec = copy_spec(FIXTURE, out / SPEC_NAME, args.model)
    passed('spec copied with only model.id changed')
    package = out / 'package'
    build_package(spec, package)
    if args.live:
        live_audit(package, out, state.binary, state.provider, args.model)
    state.register = export_package(package, out, args.base_url.url, state.package_root)
    state.env_dir = private_temp('forge-m18a-env-')
    state.env_file = state.env_dir / 'sqlite-reader.env'
    state.files = render_deploy(out, state.binary, str(state.env_file) if args.daemon == 'self' else None)
    state.archive = out / 'export' / state.register['package']['archive']
    extract_archive(state.archive, state.package_root if args.daemon == 'self' else out / 'archive-check')
    state.proof['archive'] = True
    fixture = fixture_values(out, state.register['deployment']['env'])
    names = example_names(state.files['sqlite-reader.env.example']) | state.provider.keys()
    state.token = secrets.token_urlsafe(32)
    SECRET_VALUES.append(state.token)
    write_env(state.env_file, names, {**fixture, **state.provider, 'THCLAWS_API_TOKEN': state.token,
                                      'THCLAWS_MCP_ALLOW_ALL': '1'})
    passed('env file outside repo, mode 0600')
    state.proof['env'] = True


def deploy_daemon(state):
    args = state.args
    if args.daemon == 'self':
        state.home = private_temp('forge-m18a-home-')
        state.process = start_daemon(state.files, state.package_root, state.home, state.out)
        state.started = True
    else:
        state.started = True
        operator_commands(state.out, state.archive, state.package_root, state.env_file)
    wait_ready(state.port, 30 if args.daemon == 'self' else args.timeout, state.process)
    agent_info(state.port, state.token, state.package_root, state.register, state.out, args.daemon)
    state.proof['info'] = True
    if args.daemon == 'launchd':
        installed = Path('/Library/LaunchDaemons/thclaws.sqlite-reader.plist')
        state.proof['plist'] = (installed.is_file()
                                and installed.read_bytes()
                                == (state.out / 'deploy/thclaws.sqlite-reader.plist').read_bytes())


def register_run(state):
    if state.args.atlas == 'hermetic':
        state.base = state.atlas_stack.enter_context(hermetic_atlas(state.atlas_root))
    register_atlas(state.base, state.request_token(), state.token, state.register, state.created, state.out)
    state.proof['atlas'] = True
    run_workflow(state.base, state.request_token(), state.created['workflow'], state.out / 'package',
                 state.out, state.args.timeout)
    state.proof['run'] = True


def rollback_rehearsal(state):
    state.results = rollback(state.args, state.process, state.port, state.base, state.request_token(),
                             state.created, state.archive, state.package_root)
    state.proof['rollback'] = all(step['ok'] for step in state.results)
    if rollback_failed(state.results):
        state.errors.append(Failure('rollback incomplete; see rollback.json'))
    elif state.proof['rollback']:
        passed('rollback: daemon stopped, Atlas withdrawn, archive restored')


def write_evidence(state):
    out = state.out  # Validated by output_path before any rehearsal step.
    try:
        write_json(out / 'rollback.json', state.results)
    except STEP_ERRORS as exc:
        state.errors.append(exc)
    try:
        if (out / 'export/atlas-register.json').is_file():
            shutil.copyfile(out / 'export/atlas-register.json', out / 'atlas-register.json')
    except STEP_ERRORS as exc:
        state.errors.append(exc)
    try:
        (out / 'checklist.md').write_text(
            checklist(state.args.daemon, state.args.atlas, state.proof, date.today().isoformat()),
            encoding='utf-8')
    except STEP_ERRORS as exc:
        state.errors.append(exc)


def rehearsal_result(state):
    if state.errors:
        code = 2
        for exc in state.errors:
            if isinstance(exc, Skip):
                print(f'M18a REHEARSAL SKIP: {redact(str(exc), SECRET_VALUES)} (exit 2)')
            elif isinstance(exc, FixtureUnavailable):
                print('M18a REHEARSAL SKIP: sqlite-readonly fixture runtime unavailable (exit 2)')
            elif isinstance(exc, Failure):
                print(f'M18a REHEARSAL FAIL: {redact(str(exc), SECRET_VALUES)}')
                code = 1
            elif isinstance(exc, KeyboardInterrupt):
                print('M18a REHEARSAL FAIL: interrupted')
                code = 1
            else:
                print(failure_message(exc, SECRET_VALUES))
                code = 1
        return code
    passed(f'evidence at {state.out}; next: python3 scripts/check_m18a_deploy_runbook.py '
           f'--rehearsal {state.out} '
           '(exit 2 by design: dev machine, not host evidence)')
    return 0


def finalize(state):
    if state.started or state.created:
        try:
            rollback_rehearsal(state)
        except STEP_ERRORS as exc:
            state.errors.append(exc)
    # Keep Atlas available until withdrawal, then remove private state even after a failed step.
    try:
        state.atlas_stack.close()
    except STEP_ERRORS as exc:
        state.errors.append(exc)
    try:
        if state.home is not None:
            shutil.rmtree(state.home)
    except STEP_ERRORS as exc:
        state.errors.append(exc)
    try:
        if state.env_dir is not None:
            shutil.rmtree(state.env_dir)
    except STEP_ERRORS as exc:
        state.errors.append(exc)
    if state.out is not None and state.out.is_dir():
        try:
            write_evidence(state)
        except STEP_ERRORS as exc:
            state.errors.append(exc)
        try:
            secret_scan(state.out, SECRET_VALUES)
        except STEP_ERRORS as exc:
            state.errors.append(exc)
    return rehearsal_result(state)


def main():
    args = arguments()
    if args.self_check:
        return self_check()
    state = Rehearsal(args)
    try:
        prepare(state)
        build_export(state)
        deploy_daemon(state)
        register_run(state)
    except (*STEP_ERRORS, KeyboardInterrupt) as exc:
        state.errors.append(exc)
    finally:
        code = finalize(state)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
