#!/usr/bin/env python3
"""M16: a prompt_json/assistant_json package deployed as the only bot of a thClaws host, reached by Atlas /agent/run.

Exit 0 = offline checks, the native host probes (hand install from the real export, stub provider), the Atlas poll and
workflow run through the host, and the provider golden cases through the host all passed; 1 = failure (including a
binary that is not the current baseline (THCLAWS_BASELINE) or an Atlas checkout that is not the pinned ref: fail closed); 2 = offline
passed but thclaws is absent, the binary is a CLI-only build without --serve, Atlas is not found, or the provider key
is absent, so that part was explicitly skipped. DESIGN §4 "Host deployment ของ package prompt/assistant".
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import http.client
import importlib.util
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
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from forge.atlas_export import ATLAS_REF, HOST_WORKSPACE_VERSION, REGISTER, export, host_refusals
from forge.audit import audit_package, write_report
from forge.generate import THCLAWS_BASELINE, generate
from forge.live_test import check_layout, check_servers, isolated_env, request, validate_case
from forge.pack_test import setup_fixture, test_pack
from forge.packs import read_pack
from forge.spec import load_spec

_m15 = importlib.util.spec_from_file_location('check_m15', ROOT / 'scripts/check_m15_tool_allowlist.py')
M15 = importlib.util.module_from_spec(_m15)
_m15.loader.exec_module(M15)  # reuse its stub provider and build checks rather than a second copy

BASE_URL = 'http://worker.example.invalid:4317'
SKILL_TOOLS = {'Skill', 'SkillList', 'SkillSearch'}
RUNTIME = ('/.thclaws/state/', '/.thclaws/sessions/')  # host and bot runtime state; everything else must not change


def rejects(call, needle):
    try:
        call()
    except ValueError as exc:
        assert needle in str(exc), (needle, str(exc))
        return
    raise AssertionError(f'expected a ValueError mentioning {needle!r}')


def snapshot(folder):
    files = {}
    for path in sorted(folder.rglob('*')):
        rel = path.relative_to(folder).as_posix()
        if path.is_file() and not any(state in '/' + rel for state in RUNTIME):
            files[rel] = path.read_bytes()
    return files


def build(base, packs, spec):
    package = base / spec['identity']['name']
    generate(spec, package, packs_dir=packs)
    write_report(package, audit_package(package, packs_dir=packs))
    return package


def host_export(package, out, packs, host_root):
    return export(package, out, packs_dir=packs, base_url=BASE_URL, layout='host', host_root=str(host_root))


def offline(base, packs):
    echo = load_spec(ROOT / 'fixtures/packs/echo/spec.yaml')
    packages = {name: build(base, packs, spec) for name, spec in (
        ('echo-reader', echo), ('publisher', load_spec(ROOT / 'fixtures/publisher/spec.yaml')),
        ('invoice-reviewer', load_spec(ROOT / 'fixtures/invoice-reviewer/spec.yaml')))}
    for name in ('echo-reader', 'publisher'):
        register, _ = host_export(packages[name], base / f'{name}-host', packs, '/srv/thclaws-host/')
        host_export(packages[name], base / f'{name}-host-2', packs, '/srv/thclaws-host')
        for path in (REGISTER, 'atlas-workflow.json'):
            first = base / f'{name}-host' / path
            assert not first.exists() or first.read_bytes() == (base / f'{name}-host-2' / path).read_bytes(), path
        single, _ = export(packages[name], base / f'{name}-single', packs_dir=packs, base_url=BASE_URL,
                           workspace_dir=f'/srv/atlas/{name}')
        bot = f'/srv/thclaws-host/.thclaws/bots/{name}'
        deployment = register['deployment']
        assert register['workspace']['workspace_dir'] == deployment['package_root'] == bot, register['workspace']
        assert deployment['layout'] == 'host' and deployment['daemon_cwd'] == deployment['host_root'] == '/srv/thclaws-host'
        assert deployment['bot_slug'] == name and deployment['host_files'] == {
            '.thclaws/settings.json': {'workspaceVersion': HOST_WORKSPACE_VERSION},
            '.thclaws/bots.json': {'version': 1, 'bots': [{'slug': name}]}}, deployment
        assert 'deployment.host_root' in register['registration'][0] and 'first bots.json entry' in register['registration'][0]
        # Default export unchanged: no host keys, and the host export differs only where the layout says so.
        assert 'layout' not in single['deployment'] and 'single-agent workspace' in single['registration'][0]
        assert register['node_template'] == single['node_template'] and register['worker'] == single['worker']
        assert register['registration'][1:] == single['registration'][1:]
    rejects(lambda: host_export(packages['invoice-reviewer'], base / 'invoice-host', packs, '/srv/h'),
            'input invoice_file uses atlas_file_handoff')
    assert not (base / 'invoice-host').exists()
    rejects(lambda: export(packages['echo-reader'], base / 'x1', packs_dir=packs, base_url=BASE_URL, layout='host'),
            'layout host takes host_root')
    rejects(lambda: export(packages['echo-reader'], base / 'x2', packs_dir=packs, base_url=BASE_URL, layout='host',
                           host_root='/srv/h', workspace_dir='/srv/h'), 'layout host takes host_root')
    rejects(lambda: host_export(packages['echo-reader'], base / 'x3', packs, 'srv/h'), 'absolute path')
    # Transports the host breaks (DESIGN §4 M13 4/11, M16 5): checked on the spec, before any file is written.
    collect, local = copy.deepcopy(echo), copy.deepcopy(echo)
    collect['outputs'] = [{'name': 'notes', 'transport': 'collect_files', 'files': {'globs': ['output/*.md']}}]
    local['inputs'].append({'name': 'notes', 'transport': 'local_workspace', 'path': 'inputs/notes.md'})
    assert host_refusals(collect, {}) == ['output notes uses collect_files'], host_refusals(collect, {})
    assert host_refusals(local, {}) == ['input notes uses local_workspace'], host_refusals(local, {})
    # Text the host's repair_stale rewrites on every start (M16 2): refused, and absent from every template and pack here.
    rewritten = {'AGENTS.md': b'Run python3 .thclaws/scripts/x.py first.',
                 '.thclaws/scripts/x.py': b'open(".thclaws/state/x.json")',
                 '.thclaws/skills/a/SKILL.md.tmpl': b'node .thclaws/scripts/x.js',
                 '.thclaws/schemas/x.md': b'python3 .thclaws/scripts/x.py', 'README.md': b'python3 .thclaws/x.py'}
    assert len(host_refusals(echo, rewritten)) == 3, host_refusals(echo, rewritten)
    assets = {f'.thclaws/scripts/{p.relative_to(ROOT).as_posix().removesuffix(".j2")}': p.read_bytes()
              for folder in ('templates', 'packs', 'fixtures/packs') for p in (ROOT / folder).rglob('*')
              if p.is_file() and '__pycache__' not in p.parts}
    assert not host_refusals(echo, assets), host_refusals(echo, assets)
    print('M16 offline PASS: host export (bot dir workspace, host files, registration) deterministic for echo-reader and '
          'publisher, default export unchanged, file transports and host-rewritten text refused, templates/packs clean')
    return echo


@contextlib.contextmanager
def hosted(base, package, packs, binary, env_extra):
    """Install the export archive as the only bot of a fresh host and serve the host root; yields (port, token, bot, root, register)."""
    root = base / 'host'
    out = base / 'export'
    register, code = host_export(package, out, packs, root)
    assert code == 0, 'export skipped the archive'
    for rel, value in register['deployment']['host_files'].items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(json.dumps(value, indent=2) + '\n')
    bot = Path(register['deployment']['package_root'])
    bot.mkdir(parents=True)
    with tarfile.open(out / register['package']['archive'], mode='r:gz') as archive:
        archive.extractall(bot, filter='data')
    home = base / 'home'
    home.mkdir()
    settings = base / 'settings.json'
    settings.write_text(json.dumps({'browserEnabled': False}))
    token = secrets.token_urlsafe(32)
    env = isolated_env(home, settings, token, **env_extra)
    spec = json.loads((package / 'agentspec.json').read_text())
    for capability in spec['capabilities']:
        folder = packs / capability['pack']
        fixture = base / ('fixture-' + capability['pack'])
        fixture.mkdir()
        values, _ = setup_fixture(folder, read_pack(folder), fixture, 30)
        env.update(values)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    before = snapshot(root)
    process = subprocess.Popen([binary, '--serve', '--port', str(port)], cwd=root, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        deadline = time.monotonic() + 90  # the host starts the agent process and proxies /v1 once it is ready
        while True:
            assert process.poll() is None, 'host exited before readiness'
            try:
                info = request(port, token, '/v1/agent/info', 2)
                break
            except (OSError, ValueError):
                assert time.monotonic() < deadline, 'host readiness timed out'
                time.sleep(0.3)
        check_layout(info, bot)  # workspace_dir = bot dir; the bot itself was not turned into a host
        check_servers(info, {s for p in register['deployment']['packs'] for s in p['mcp_servers']}, {})
        skills = {s['name'] for s in info.get('skills', []) if isinstance(s, dict)}
        assert spec['identity']['name'] in skills, skills
        yield port, token, bot, root, register
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.wait(timeout=10)
    after = snapshot(root)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert not changed, f'host changed files outside runtime state: {changed[:20]}'


def atlas_root():
    candidate = Path(os.environ.get('ATLAS_ROOT') or ROOT.parent / 'atlas-control-plane')
    return candidate if (candidate / 'atlas' / 'workflows.py').is_file() else None


def atlas_call(port, method, path, body=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=60)
    try:
        connection.request(method, path, body=None if body is None else json.dumps(body),
                           headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        payload = json.loads(response.read() or b'{}')
        assert response.status < 400, f'{method} {path}: {response.status} {payload}'
        return payload
    finally:
        connection.close()


def atlas_checks(atlas, port, token, register, expected_artifact):
    """Atlas at the pinned ref: poll sees the host online, and a test-mode run of the node template succeeds through it."""
    ref = subprocess.run(['git', '-C', str(atlas), 'rev-parse', 'HEAD'], capture_output=True, text=True,
                         check=False).stdout.strip()
    assert ref == ATLAS_REF, f'Atlas checkout {ref[:7] or "unknown"} is not the pinned {ATLAS_REF[:7]}; re-check DESIGN §8'
    sys.path.insert(0, str(atlas))
    from atlas.app import AtlasHttpServer, AtlasRuntime
    from atlas.config import Config
    with tempfile.TemporaryDirectory(prefix='m16-atlas-') as temporary:
        runtime = AtlasRuntime(Config(host='127.0.0.1', port=0, db_path=Path(temporary) / 'atlas.sqlite', api_token=None,
                                      request_timeout_seconds=120, secret_key=secrets.token_hex(32),
                                      enable_loopback_without_token=True, upload_dir=Path(temporary) / 'uploads'))
        server = AtlasHttpServer(('127.0.0.1', 0), runtime)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        aport = server.server_address[1]
        try:
            worker = atlas_call(aport, 'POST', '/api/workers',
                                {**register['worker'], 'base_url': f'http://127.0.0.1:{port}', 'token': token})['worker']
            polled = atlas_call(aport, 'POST', f"/api/workers/{worker['id']}/poll")['worker']
            assert polled['status'] == 'online', polled
            info = atlas_call(aport, 'GET', f"/api/workers/{worker['id']}")['worker'].get('agent_info') or polled.get('agent_info')
            agent = (info or {}).get('agent', {})
            assert sorted(s['name'] for s in agent.get('mcp_servers', [])) == register['deployment']['mcp_servers'], agent
            assert Path(agent['workspace_dir']).resolve() == Path(register['deployment']['package_root']).resolve(), agent
            atlas_call(aport, 'POST', '/api/workspaces', register['workspace'])
            node = register['node_template']
            flow = {'name': node['id'], 'graph': {'start': node['id'], 'nodes': [node], 'edges': []},
                    'policy': register['policy_template']}
            created = atlas_call(aport, 'POST', '/api/workflows', flow)['workflow']
            run = atlas_call(aport, 'POST', '/api/workflow-runs', {'workflow_definition_id': created['id'],
                                                                   'input': expected_artifact[0], 'execution_mode': 'test'})['run']
            deadline = time.monotonic() + 180
            while (state := atlas_call(aport, 'GET', f"/api/workflow-runs/{run['id']}"))['run']['state'] not in (
                    'succeeded', 'failed', 'cancelled', 'paused', 'waiting_for_human', 'recovery_required'):
                assert time.monotonic() < deadline, ('Atlas run timed out', state['run']['state'])
                time.sleep(0.5)
            assert state['run']['state'] == 'succeeded', (state['run'], state['nodes'])
            artifacts = atlas_call(aport, 'GET', f"/api/workflow-runs/{run['id']}/artifacts")['artifacts']
            assert [(a['key'], a['content']) for a in artifacts] == [expected_artifact[1]], artifacts
            time.sleep(1)  # let Atlas finish usage metering before its database goes away
        finally:
            server.shutdown()
            server.server_close()
            runtime.close()
    print(f'M16 native PASS: Atlas {ATLAS_REF[:7]} polled the host online with the bot MCP inventory and workspace_dir, and a '
          'test-mode workflow run of the exported node template succeeded through the host with its JSON artifact')


def native(base, spec, packs, binary):
    """Stub provider: allowlist and identity under the host, tree unchanged, Atlas poll + run. Returns 2 when Atlas is absent."""
    probe = copy.deepcopy(spec)
    probe['model']['id'] = 'oai/m16-probe'
    package = build(base, packs, probe)
    Provider = M15.Provider
    with ThreadingHTTPServer(('127.0.0.1', 0), Provider) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        extra = {'OPENAI_COMPAT_API_KEY': 'local-probe-only',
                 'OPENAI_COMPAT_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1'}
        try:
            with hosted(base, package, packs, binary, extra) as (port, token, bot, root, register):
                def run(tool, arguments, workspace):
                    Provider.records.clear()
                    Provider.action = tool and (tool, arguments)
                    events = request(port, token, '/agent/run', 60, {'prompt': 'host probe', 'workspace_dir': str(workspace),
                                                                     'model': 'oai/m16-probe', 'stream': True})
                    offered = {t['function']['name'] for t in Provider.records[0].get('tools', [])}
                    system = '\n'.join(m['content'] for m in Provider.records[0]['messages'] if m['role'] == 'system')
                    result = next((e['data'] for e in events if e['event'] == 'tool_use_result'), None)
                    return offered, system, result

                allowed = set(json.loads((bot / '.thclaws/settings.json').read_text())['allowedTools'])
                offered, system, result = run('echo__echo', {'text': 'host'}, bot)
                assert offered == allowed | SKILL_TOOLS and result['status'] == 'ok', (sorted(offered), result)
                assert spec['mission']['purpose'] in system, 'AGENTS.md of the bot was not loaded with workspace_dir = bot dir'
                offered, _, result = run('Bash', {'command': 'touch bash-probe.txt'}, bot)
                assert result['status'] == 'error' and 'unknown tool: Bash' in result['output'], result
                _, system, _ = run(None, None, root)
                assert spec['mission']['purpose'] not in system, 'workspace_dir = host root unexpectedly loaded the bot identity'
                print(f'M16 native PASS: host proxies /agent/run to the bot; offered {sorted(offered)} (allowedTools + Skill), '
                      'forced Bash -> unknown tool, identity loads with workspace_dir = bot dir and not with the host root')
                atlas = atlas_root()
                if atlas is None:
                    print('M16 SKIP: atlas-control-plane checkout not found (set ATLAS_ROOT); Atlas poll/run through the host not run')
                    return 2
                Provider.action = None
                normal = next(c for c in spec['evaluation']['golden_cases'] if c['kind'] == 'normal')
                atlas_checks(atlas, port, token, register,
                             (normal['input'], (spec['outputs'][0]['name'], {'refused': True, 'reason': 'probe'})))
        finally:
            Provider.action = None
            Provider.records.clear()
            server.shutdown()
            thread.join()
    print('M16 native PASS: host tree unchanged outside runtime state after install, probes and the Atlas run')
    return 0


def provider(base, spec, packs, binary, key_env, model, timeout):
    live = copy.deepcopy(spec)
    live['model']['id'] = model
    package = build(base, packs, live)
    extra = {key_env: os.environ[key_env]}
    if model.startswith('oai/'):
        extra['OPENAI_COMPAT_BASE_URL'] = os.environ['OPENAI_COMPAT_BASE_URL']
    called, failures = set(), []
    with hosted(base, package, packs, binary, extra) as (port, token, bot, _, _):
        for case in live['evaluation']['golden_cases']:
            try:
                events = request(port, token, '/agent/run', timeout, {'prompt': json.dumps(case['input']),
                                 'workspace_dir': str(bot), 'model': model, 'stream': True}, idle_timeout=120)
                validate_case(live, case, events)
                called.update(e['data']['name'] for e in events
                              if e['event'] == 'tool_use_result' and e['data'].get('status') == 'ok')
                print(f"  {case['id']}: passed")
            except (ValueError, OSError) as exc:
                failures.append(case['id'])
                print(f"  {case['id']}: failed {exc if isinstance(exc, ValueError) else type(exc).__name__}")
    assert not failures, f'golden cases failed through the host: {failures}'
    assert called & {'echo__echo', 'echo__reverse'}, f'no successful echo tool event: {sorted(called)}'
    print(f'M16 provider PASS: {len(live["evaluation"]["golden_cases"])}/{len(live["evaluation"]["golden_cases"])} golden '
          f'cases through the host with workspace_dir = bot dir, called={sorted(called)}')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--model', help='explicit provider model for the live fixture; the checked-in fixture is unchanged')
    parser.add_argument('--provider-key-env', default='OPENAI_API_KEY')
    parser.add_argument('--timeout', type=float, default=180)
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix='forge-m16-') as directory:
            base = Path(directory).resolve()
            packs = base / 'packs'
            shutil.copytree(ROOT / 'packs', packs, ignore=shutil.ignore_patterns('pack-conformance.json', '__pycache__'))
            shutil.copytree(ROOT / 'fixtures/packs/echo', packs / 'echo',
                            ignore=shutil.ignore_patterns('pack-conformance.json', '__pycache__'))
            evidence, code = test_pack('echo', packs, timeout=10)
            assert code == 0, evidence
            spec = offline(base / 'offline', packs)
            binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
            if not binary:
                print('M16 SKIP: thclaws absent; host install, Atlas and provider checks not run (exit 2)')
                return 2
            runtime = M15.binary_pair(binary)
            assert runtime == THCLAWS_BASELINE, f'thclaws {runtime} is not the {THCLAWS_BASELINE} baseline; re-check DESIGN §4 first'
            if not M15.desktop_build(binary):
                print('M16 SKIP: CLI-only thclaws build has no --serve and no host (DESIGN §4 host + bots layout 13) (exit 2)')
                return 2
            code = native(base / 'native', spec, packs, binary)
            if not os.environ.get(args.provider_key_env, '').strip():
                print(f'M16 SKIP: no provider key in {args.provider_key_env}; golden cases through the host not run (exit 2)')
                return 2
            provider(base / 'provider', spec, packs, binary, args.provider_key_env, args.model or spec['model']['id'],
                     args.timeout)
            return code
    except (AssertionError, ValueError, OSError, KeyError, StopIteration, subprocess.SubprocessError) as exc:
        print(f'M16 FAIL: {type(exc).__name__}: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
