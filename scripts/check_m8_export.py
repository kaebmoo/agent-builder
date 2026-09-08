#!/usr/bin/env python3
"""M8 gate: deterministic Atlas export, pinned-schema validity, bound T2 flow, archive and hermetic registration."""
from __future__ import annotations

import copy
import hashlib
import http.client
import io
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator

from forge.atlas_export import ARCHIVE_REWRITTEN, ATLAS_REF, ATLAS_SCHEMA_PATH, UPSTREAM, WORKFLOW, export
from forge.audit import audit_package, write_report
from forge.generate import generate
from forge.spec import load_spec

SCHEMA = json.loads(ATLAS_SCHEMA_PATH.read_text(encoding='utf-8'))
BASE_URL = 'http://worker.example.invalid:4317/'
# Builder-authored prose must not claim what the guarantee matrix does not support (AGENTS.md); routing tags are
# spec intent and are checked separately below.
FORBIDDEN_CLAIMS = ('deploy-ready', 'shippable', 'read-only')


def snapshot(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob('*')) if p.is_file()}


def fragment(value, definition):
    Draft202012Validator({'$defs': SCHEMA['$defs'], '$ref': f'#/$defs/{definition}'}).validate(value)


def build(work, name, spec):
    package = work / name
    generate(spec, package)
    write_report(package, audit_package(package))
    return package


def run_export(package, out, **overrides):
    values = {'base_url': BASE_URL, 'workspace_dir': f'/srv/atlas/{package.name}', **overrides}
    return export(package, out, **values)


def expect_error(kind, message, callable_, *args, **kwargs):
    try:
        callable_(*args, **kwargs)
    except kind as exc:
        assert message in str(exc), f'{message!r} not in {exc}'
    else:
        raise AssertionError(f'expected {kind.__name__}: {message}')


def synthetic_specs(invoice):
    collect = copy.deepcopy(invoice)
    collect['outputs'] = [{'name': 'review_files', 'transport': 'collect_files',
                           'files': {'globs': ['output/**/*.json']}}]
    flow = copy.deepcopy(invoice)
    flow['target'] = 'atlas-workflow'
    flow['inputs'] = [item for item in flow['inputs'] if item['transport'] == 'prompt_json']
    for case in flow['evaluation']['golden_cases']:
        case['input'].pop('invoice_file', None)
    flow_file = copy.deepcopy(invoice)
    flow_file['target'] = 'atlas-workflow'
    return collect, flow, flow_file


def offline_checks(work):
    invoice = load_spec(ROOT / 'fixtures/invoice-reviewer/spec.yaml')
    publisher = load_spec(ROOT / 'fixtures/publisher/spec.yaml')
    collect, flow, flow_file = synthetic_specs(invoice)
    packages = {name: build(work, name, spec) for name, spec in
                (('invoice-reviewer', invoice), ('publisher', publisher), ('collect', collect), ('flow', flow))}
    exports, registers, code = {}, {}, 0
    for name, package in packages.items():
        registers[name], result = run_export(package, work / f'{name}-export')
        exports[name] = snapshot(work / f'{name}-export')
        code = max(code, result)
        register = registers[name]
        assert register['package']['package_status'] == json.loads(
            (package / 'builder-build-report.json').read_text())['package_status'], 'export changed the status'
        assert not register['deployment']['deployment_verified'] and register['atlas_ref'] == ATLAS_REF
        fragment(register['node_template'], 'workerNode')
        fragment(register['policy_template'], 'policy')
        if register['edge_template'] is not None:
            fragment(register['edge_template'], 'edge')
        assert 'token' not in register['worker'] and 'token' not in register['workspace'], 'secret field exported'
        text = b''.join(content for path, content in exports[name].items() if path.endswith('.json')).decode()
        authored = ' '.join([*register['registration'], *register['deployment']['reachability_checks'],
                             *register['deployment']['hints']]).lower()
        assert not any(claim in authored for claim in FORBIDDEN_CLAIMS), 'export claims an unverified status'
        assert 'deploy-ready' not in text.lower() and 'shippable' not in text.lower()
        assert 'never sets deployment_verified' in authored and 'reachability' in authored
        assert '__OPERATOR_T2_' not in text
        identity = json.loads((package / 'agentspec.json').read_text())['identity']['name']
        assert register['worker'] == {'id': f'wrk_{identity}', 'name': identity, 'base_url': BASE_URL.rstrip('/'),
                                      'role': register['node_template']['role'],
                                      'tags': register['node_template']['tags']}
        assert register['workspace'] == {'id': f'wsp_{identity}', 'worker_id': f'wrk_{identity}',
                                         'workspace_key': identity, 'workspace_dir': f'/srv/atlas/{package.name}',
                                         'tags': register['node_template']['tags']}
        assert register['deployment']['daemon_cwd'] == register['deployment']['package_root'] == f'/srv/atlas/{package.name}'

    node = registers['invoice-reviewer']['node_template']
    assert node['id'] == 'invoice-reviewer' and node['model'] == invoice['model']['id']
    assert node['prompt'] == ('{"invoice_file": {input.invoice_file}, "request": {input.request}}'
                              '\nHandoff files directory: {files_dir}')
    assert node['outputs'] == ['invoice_review'] and node['output_format'] == 'json' and 'collect_files' not in node
    edge = registers['invoice-reviewer']['edge_template']
    assert edge == {'from': UPSTREAM, 'to': 'invoice-reviewer', 'condition': {'type': 'always'},
                    'push_files': [f'files.{UPSTREAM}.*']}
    assert registers['invoice-reviewer']['policy_template'] == {
        'allowed_worker_ids': ['wrk_invoice-reviewer'], 'allowed_workspace_ids': ['wsp_invoice-reviewer'],
        'file_handoff': True}
    assert registers['invoice-reviewer']['workflow'] is None and WORKFLOW not in exports['invoice-reviewer']
    assert registers['invoice-reviewer']['deployment']['mcp_servers'] == [] and registers['invoice-reviewer']['deployment']['env'] == []

    node = registers['collect']['node_template']
    assert node['collect_files'] == ['output/**/*.json'] and 'output_format' not in node and 'outputs' not in node

    node = registers['flow']['node_template']
    assert '{files_dir}' not in node['prompt'] and registers['flow']['edge_template'] is None
    assert 'file_handoff' not in registers['flow']['policy_template']
    definition = json.loads(exports['flow'][WORKFLOW])
    Draft202012Validator(SCHEMA).validate(definition)
    assert definition == {'name': 'invoice-reviewer', 'graph': {'start': 'invoice-reviewer', 'nodes': [node], 'edges': []},
                          'policy': registers['flow']['policy_template']}

    register = registers['publisher']
    definition = json.loads(exports['publisher'][WORKFLOW])
    Draft202012Validator(SCHEMA).validate(definition)
    gate, worker = definition['graph']['nodes']
    assert gate['type'] == 'human_gate' and definition['graph']['start'] == gate['id']
    assert worker == register['node_template'] and worker['worker_id'] == 'wrk_publisher'
    assert worker['workspace_id'] == 'wsp_publisher' and worker['output_format'] == 'json'
    assert worker['outputs'] == ['publication'] and worker['model'] == publisher['model']['id']
    assert definition['graph']['edges'] == [{'from': gate['id'], 'to': worker['id'],
                                             'condition': {'type': 'human_selected', 'choice': 'approve'}}]
    assert definition['policy'] == register['policy_template'] == {
        'allowed_worker_ids': ['wrk_publisher'], 'allowed_workspace_ids': ['wsp_publisher'],
        'max_jobs': 1, 'max_attempts_per_node': 1}
    deployment = register['deployment']
    assert deployment['isolated_daemon_required'] and register['edge_template'] is None
    assert deployment['mcp_servers'] == ['publisher-email-sftp'] and deployment['env'] == sorted(publisher['env'])
    assert deployment['network'] == 'general-http' and deployment['packs'][0]['pack'] == 'publisher-email-sftp'
    assert all('conformance' not in pack for pack in deployment['packs'])
    assert register['package']['tier'] == 'T2' and register['package']['target'] == 'atlas-workflow'
    assert not registers['invoice-reviewer']['deployment']['isolated_daemon_required']

    # Determinism: same package twice, and the JSON files again after a fresh generate + audit.
    run_export(packages['publisher'], work / 'publisher-repeat')
    assert snapshot(work / 'publisher-repeat') == exports['publisher'], 'export is not deterministic'
    rebuilt = build(work, 'publisher-rebuilt', publisher)
    run_export(rebuilt, work / 'publisher-rebuilt-export', workspace_dir='/srv/atlas/publisher')
    again = snapshot(work / 'publisher-rebuilt-export')
    assert {k: v for k, v in again.items() if k.endswith('.json')} == {
        k: v for k, v in exports['publisher'].items() if k.endswith('.json')}, 'register files depend on the build'
    bound, _ = run_export(packages['flow'], work / 'flow-bound', worker_id='wrk_a1', workspace_id='wsp_b2',
                          workspace_key='shared', base_url=' https://atlas-worker.example.invalid/prefix/ ')
    assert bound['worker']['base_url'] == 'https://atlas-worker.example.invalid/prefix'
    assert bound['node_template']['worker_id'] == 'wrk_a1' and bound['workspace']['workspace_key'] == 'shared'
    assert bound['policy_template']['allowed_workspace_ids'] == ['wsp_b2']
    # Structural T2 binding: a binding value that merely contains a placeholder is never rewritten.
    tricky = 'wsp__OPERATOR_T2_WORKSPACE_ID__x'
    bound, _ = run_export(packages['publisher'], work / 'publisher-tricky', workspace_id=tricky)
    assert bound['node_template']['workspace_id'] == tricky and bound['policy_template']['allowed_workspace_ids'] == [tricky]

    package = packages['invoice-reviewer']
    expect_error(FileExistsError, 'already exists', run_export, package, work / 'invoice-reviewer-export')
    for base_url in ('ftp://worker.example.invalid', 'http://user:pw@worker.example.invalid', 'worker.example.invalid',
                     'http://worker.example.invalid/?x=1', 'HTTP://worker.example.invalid', 'http://worker.example.invalid/#',
                     'http://worker.example.invalid:abc', 'http://worker.example.invalid:99999', 'http://worker.example.invalid:',
                     'http://worker.example.invalid:0'):
        expect_error(ValueError, 'base_url', run_export, package, work / 'bad', base_url=base_url)
    expect_error(ValueError, 'outside the package', run_export, package, package / 'export')
    unaudited = work / 'unaudited'
    generate(invoice, unaudited)
    expect_error(ValueError, 'forge audit --write', run_export, unaudited, work / 'unaudited-export')
    failed = build(work, 'failed', invoice)
    report = json.loads((failed / 'builder-build-report.json').read_text())
    report.update(package_status='unverified')
    report['audit']['manifest'] = 'failed'
    report['static_audit']['thclaws_validate'] = {'status': 'failed', 'version': None, 'detail': 'synthetic'}
    (failed / 'builder-build-report.json').write_text(json.dumps(report))
    expect_error(ValueError, 'forge audit --write', run_export, failed, work / 'failed-export')
    # AgentSpec validation (forge.spec) rejects what Atlas render_prompt/artifactKey cannot handle: names outside
    # [A-Za-z_][A-Za-z0-9_]* (left literal, no error) and scalar input schemas (str()'d into non-JSON).
    dashed = copy.deepcopy(flow)
    dashed['inputs'][0]['name'] = 'request-1'
    for case in dashed['evaluation']['golden_cases']:
        if 'request' in case['input']:
            case['input']['request-1'] = case['input'].pop('request')
    expect_error(ValueError, 'Atlas prompt variable', generate, dashed, work / 'dashed')
    scalar = copy.deepcopy(flow)
    scalar['inputs'][0]['schema'] = {'type': 'string', 'minLength': 1}
    expect_error(ValueError, 'object/array', generate, scalar, work / 'scalar')
    dashed_output = copy.deepcopy(flow)
    dashed_output['outputs'][0]['name'] = 'invoice-review'
    expect_error(ValueError, 'artifact key', generate, dashed_output, work / 'dashed-output')
    no_schema = copy.deepcopy(flow)
    no_schema['inputs'][0].pop('schema')
    expect_error(ValueError, 'must declare a schema', generate, no_schema, work / 'no-schema')
    # thclaws agent pack strips paths containing _secret silently; the generated schema file for an input named
    # api_secret is such a path, so the audited package packs incomplete and export must refuse it.
    secret = copy.deepcopy(flow)
    secret['inputs'][0]['name'] = 'api_secret'
    for case in secret['evaluation']['golden_cases']:
        if 'request' in case['input']:
            case['input']['api_secret'] = case['input'].pop('request')
    stripped = build(work, 'stripped', secret)
    assert (stripped / '.thclaws/schemas/inputs--api_secret.json').is_file()
    if shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws')):
        expect_error(ValueError, 'archive inventory', run_export, stripped, work / 'stripped-export')
        assert not (work / 'stripped-export').exists(), 'refused export left files behind'
        print('M8 PASS: export refuses an archive that thclaws agent pack silently stripped')
    for directory in ('srv/atlas', '/srv/../etc', ''):
        expect_error(ValueError, 'workspace_dir', run_export, package, work / 'bad', workspace_dir=directory)
    expect_error(ValueError, 'worker_id', run_export, package, work / 'bad', worker_id='wrk one')
    expect_error(ValueError, 'push_files', generate_and_export, work, 'flow-file', flow_file)
    drifted = build(work, 'drifted', invoice)
    manifest = drifted / 'manifest.json'
    manifest.write_text(manifest.read_text().replace('"custom"', '"edited"'))
    expect_error(ValueError, 'differs from the file rendered', run_export, drifted, work / 'drifted-export')
    stale = build(work, 'stale', invoice)
    report = json.loads((stale / 'builder-build-report.json').read_text())
    report['package_status'] = 'candidate'
    (stale / 'builder-build-report.json').write_text(json.dumps(report))
    expect_error(ValueError, 'schema', run_export, stale, work / 'stale-export')
    report['package_status'] = 'unverified'
    report['deployment_hints'].append('hand-edited hint')
    (stale / 'builder-build-report.json').write_text(json.dumps(report))
    expect_error(ValueError, 'stale', run_export, stale, work / 'stale-export')
    print('M8 PASS: deterministic export, schema-valid templates, bound T2 flow, collect_files/push_files mapping, refusals')
    return packages, registers, exports, code


def generate_and_export(work, name, spec):
    package = build(work, name, spec)
    run_export(package, work / f'{name}-export')


def archive_checks(package, register, files):
    name = register['package']['archive']
    assert name == f"{package.name}-{register['package']['version']}.tar.gz", register['package']
    digest = hashlib.sha256(files[name]).hexdigest()
    assert files[name + '.sha256'] == f'{digest}  {name}\n'.encode()
    with tarfile.open(fileobj=io.BytesIO(files[name]), mode='r:gz') as archive:
        members = {m.name: archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()}
    source = snapshot(package)
    assert set(members) == set(source), sorted(set(members) ^ set(source))
    # DESIGN §8: thclaws agent pack rewrites exactly manifest.json (fused identity) and .thclaws/settings.json
    # (agent block stripped); any other difference means the pack shape changed and DESIGN must be re-checked.
    rewritten = {path for path in members if members[path] != source[path]}
    assert rewritten == set(ARCHIVE_REWRITTEN), rewritten
    manifest, original = json.loads(members['manifest.json']), json.loads(source['manifest.json'])
    assert manifest['id'] == package.name and all(manifest[key] == value for key, value in original.items())
    settings, original = json.loads(members['.thclaws/settings.json']), json.loads(source['.thclaws/settings.json'])
    assert settings == {key: value for key, value in original.items() if key != 'agent'}
    print('M8 PASS: thclaws agent pack archive with verified sha256 sidecar; only the documented identity files differ')


def atlas_root():
    candidate = Path(os.environ.get('ATLAS_ROOT') or ROOT.parent / 'atlas-control-plane')
    return candidate if (candidate / 'atlas' / 'workflows.py').is_file() else None


def call(port, method, path, body=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
    try:
        connection.request(method, path, body=None if body is None else json.dumps(body),
                           headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        payload = json.loads(response.read() or b'{}')
        assert response.status < 400, f'{method} {path}: {response.status} {payload}'
        return payload
    finally:
        connection.close()


def compose(register, upstream='upstream'):
    """A two-node workflow around the atlas-worker templates: upstream collect_files feed this worker."""
    node, edge, policy = register['node_template'], register['edge_template'], copy.deepcopy(register['policy_template'])
    source = {'id': upstream, 'type': 'worker', 'worker_id': node['worker_id'], 'workspace_id': node['workspace_id'],
              'prompt': 'Produce the invoice file under output/', 'collect_files': ['output/*.pdf']}
    edge = json.loads(json.dumps(edge).replace(UPSTREAM, upstream))
    return {'name': 'composed', 'graph': {'start': upstream, 'nodes': [source, node], 'edges': [edge]}, 'policy': policy}


def daemon_probe(atlas_port, register, files):
    """Start the daemon from the UNPACKED ARCHIVE (the tree an operator deploys), not the source package."""
    binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
    with tempfile.TemporaryDirectory(prefix='forge-m8-daemon-') as temporary:
        base = Path(temporary).resolve()
        work = base / 'package'
        work.mkdir()
        with tarfile.open(fileobj=io.BytesIO(files[register['package']['archive']]), mode='r:gz') as archive:
            archive.extractall(work, filter='data')
        home = base / 'home'
        home.mkdir()
        settings = base / 'settings.json'
        settings.write_text(json.dumps({'browserEnabled': False}))
        token = secrets.token_urlsafe(32)
        env = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(home), 'XDG_CONFIG_HOME': str(home / '.config'),
               'THCLAWS_CONFIG': str(settings), 'THCLAWS_MCP_ALLOW_ALL': '1', 'THCLAWS_API_TOKEN': token}
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen([binary, '--serve', '--port', str(port)], cwd=work, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.monotonic() + 30
            while True:
                assert process.poll() is None, 'temporary daemon exited before readiness'
                try:
                    call(port, 'GET', '/healthz')
                    break
                except (OSError, ValueError, AssertionError, http.client.HTTPException):
                    assert time.monotonic() < deadline, 'temporary daemon readiness timed out'
                    time.sleep(0.1)
            worker = call(atlas_port, 'POST', '/api/workers',
                          {**register['worker'], 'base_url': f'http://127.0.0.1:{port}', 'token': token})['worker']
            assert worker['id'] == register['worker']['id'] and worker.get('token_set')
            polled = call(atlas_port, 'POST', f"/api/workers/{worker['id']}/poll")['worker']
            assert polled['status'] == 'online', polled
            info = call(atlas_port, 'GET', f"/api/workers/{worker['id']}")['worker'].get('agent_info') or polled.get('agent_info')
            servers = sorted(s['name'] for s in (info or {}).get('agent', {}).get('mcp_servers', []))
            assert servers == register['deployment']['mcp_servers'], (servers, register['deployment']['mcp_servers'])
            assert token not in json.dumps(polled) and token not in json.dumps(info)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
    print('M8 PASS: Atlas poll saw the daemon started from the unpacked archive online with the declared MCP inventory '
          '(deployment_verified stays false)')


def atlas_checks(root, packages, registers, exports, probe):
    upstream = (root / 'docs/specs/workflow-definition.schema.json').read_bytes()
    assert upstream == ATLAS_SCHEMA_PATH.read_bytes(), (
        'Atlas workflow-definition schema drifted from patterns/atlas-workflow-definition.schema.json; '
        're-pin it and update DESIGN §8 before trusting this export')
    ref = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True,
                         check=False).stdout.strip()
    assert ref == ATLAS_REF, (f'Atlas checkout {ref[:7] or "unknown"} is not the pinned {ATLAS_REF[:7]}; '
                              're-check DESIGN §8 and update ATLAS_REF before trusting this gate')
    print(f'M8 native Atlas: {root} at {ref[:7]} (pinned ref and schema match)')
    sys.path.insert(0, str(root))
    from atlas.app import AtlasHttpServer, AtlasRuntime
    from atlas.config import Config
    from atlas.workflows import render_prompt, validate_workflow_graph, validate_workflow_policy

    definitions = {name: json.loads(exports[name][WORKFLOW]) for name in ('publisher', 'flow')}
    definitions['invoice-reviewer'] = compose(registers['invoice-reviewer'])
    for definition in definitions.values():
        validate_workflow_graph(definition['graph'], definition['policy'])
        validate_workflow_policy(definition['policy'])
    unguarded = copy.deepcopy(definitions['invoice-reviewer'])
    unguarded['policy'].pop('file_handoff')
    expect_error(ValueError, 'file_handoff', validate_workflow_graph, unguarded['graph'], unguarded['policy'])
    for name, package in packages.items():
        spec = json.loads((package / 'agentspec.json').read_text())
        node = registers[name]['node_template']
        for case in spec['evaluation']['golden_cases']:
            if set(case['input']) != {item['name'] for item in spec['inputs']}:
                expect_error(ValueError, 'prompt variable', render_prompt, node['prompt'], input=case['input'],
                             artifacts={}, run={}, node=node, job={})
                continue
            rendered = render_prompt(node['prompt'], input=case['input'], artifacts={}, run={}, node=node, job={})
            first = rendered.replace('{files_dir}', 'inputs/incoming/run/node').splitlines()[0]
            assert json.loads(first[first.index('{'):]) == case['input'], (name, case['id'], rendered)
    print('M8 PASS: Atlas graph/policy validation and prompt rendering reproduce the audited input envelopes')

    with tempfile.TemporaryDirectory(prefix='forge-m8-atlas-') as temporary:
        runtime = AtlasRuntime(Config(host='127.0.0.1', port=0, db_path=Path(temporary) / 'atlas.sqlite',
                                      api_token=None, request_timeout_seconds=10, secret_key=secrets.token_hex(32),
                                      enable_loopback_without_token=True, upload_dir=Path(temporary) / 'uploads'))
        server = AtlasHttpServer(('127.0.0.1', 0), runtime)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]
        try:
            for name, definition in definitions.items():
                register = registers[name]
                # Atlas upserts by id OR base_url, so each package gets its own daemon URL like a real deployment.
                worker = call(port, 'POST', '/api/workers', {**register['worker'], 'token': 'gate-worker-token',
                                                             'base_url': f'http://{name}.example.invalid:4317'})['worker']
                assert worker['id'] == register['worker']['id'] and 'token' not in worker
                workspace = call(port, 'POST', '/api/workspaces', register['workspace'])['workspace']
                assert workspace['id'] == register['workspace']['id']
                created = call(port, 'POST', '/api/workflows', definition)['workflow']
                fetched = call(port, 'GET', f"/api/workflows/{created['id']}")['workflow']
                assert fetched['graph'] == definition['graph'] and fetched['policy'] == definition['policy']
            listed = {w['id'] for w in call(port, 'GET', '/api/workers')['workers']}
            assert listed == {registers[name]['worker']['id'] for name in definitions}
            print('M8 PASS: hermetic Atlas accepted the exported worker, workspace and workflow registrations')
            if probe:
                daemon_probe(port, registers['publisher'], exports['publisher'])
        finally:
            server.shutdown()
            server.server_close()
            runtime.close()


def check(work):
    packages, registers, exports, code = offline_checks(work)
    thclaws = code == 0
    if thclaws:
        archive_checks(packages['publisher'], registers['publisher'], exports['publisher'])
    else:
        print('M8 SKIP: thclaws not on PATH; archive not produced and packages remain unverified')
    root = atlas_root()
    if root is None:
        print('M8 SKIP: atlas-control-plane checkout not found (set ATLAS_ROOT); native Atlas registration not run')
        return 2
    atlas_checks(root, packages, registers, exports, probe=thclaws)
    return 0 if thclaws else 2


if __name__ == '__main__':
    try:
        with tempfile.TemporaryDirectory(prefix='forge-m8-') as temporary:
            status = check(Path(temporary).resolve())
    except (AssertionError, ValueError, OSError, KeyError, TypeError) as exc:
        print(f'M8 FAIL: {type(exc).__name__}: {exc}')
        status = 1
    raise SystemExit(status)
