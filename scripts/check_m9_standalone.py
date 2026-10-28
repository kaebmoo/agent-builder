#!/usr/bin/env python3
"""M9: deterministic standalone packages, real runtime denial probes, and optional provider golden cases."""
import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from forge.audit import audit_package, exit_code, write_report
from forge.generate import STANDALONE_THCLAWS_COMPATIBILITY, generate, render, standalone_roles
from forge.live_test import live_audit, validate_value, workflow_process, workflow_value
from forge.spec import load_spec


def rejects(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError('expected rejection')


def snapshot(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()}


def offline(base, spec):
    package = base / 'package'
    generate(spec, package)
    generate(spec, base / 'repeat')
    assert snapshot(package) == snapshot(base / 'repeat')
    report = audit_package(package)
    assert exit_code(report) != 1, report['static_audit']
    assert not any(p.name.startswith('atlas-') for p in package.iterdir())
    roles = standalone_roles(spec, ROOT / 'packs')
    for role, tools in roles.items():
        metadata = yaml.safe_load((package / f'.thclaws/agents/{role}.md').read_text().split('---')[1])
        assert metadata['tools'].split(', ') == tools
        assert tools and set(tools) <= set(spec['permissions']['tools'])
        assert 'Bash' not in tools and 'Write' not in tools and 'writePaths' not in metadata
    # T1 is M14's scope (check_m14_standalone_t1.py); here only the T0 contract must not leak into a T1 spec:
    # a package generated as T0 whose agentspec is rewritten to T1 drifts (worker role, matrix) and cannot pass audit.
    worker = copy.deepcopy(spec)
    worker['permissions'].update(tier='T1', tools=['Read', 'Write', 'Edit'], write_scope='output')
    legacy = base / 'legacy-t1'
    shutil.copytree(package, legacy)
    (legacy / 'agentspec.json').write_text(json.dumps(worker))
    assert exit_code(audit_package(legacy)) == 1
    blocked, code = live_audit(legacy)
    assert code == 1 and blocked['audit']['live'] == 'failed'
    for tool in ['MemoryWrite', 'KmsWrite', 'NotebookEdit', 'Task', 'WorkflowRun', 'UnknownTool']:
        invalid = copy.deepcopy(spec)
        invalid['permissions']['tools'].append(tool)
        rejects(lambda invalid=invalid: render(invalid))
    sql = load_spec(ROOT / 'fixtures/sql-reader/spec.yaml')
    sql.update(target='thclaws-standalone', package_pattern='static-pipeline')
    sql.pop('routing')
    assert all('sql-readonly__query' in tools for tools in standalone_roles(sql, ROOT / 'packs').values())
    invalid = copy.deepcopy(spec)
    invalid['permissions']['tools'] = []
    rejects(lambda: render(invalid))
    assert next(r['status'] for r in report['guarantee_matrix'] if r['claim'] == 'no_file_writes') == 'Not guaranteed'
    # The new CLI compatibility scope must not silently widen Atlas compatibility.
    from forge.audit import thclaws_validate
    version_result = subprocess.CompletedProcess([], 0, 'thclaws 0.121.0\nrevision: 35ccf16\n', '')
    with patch('forge.audit.shutil.which', return_value='test-thclaws'), patch(
            'forge.audit.subprocess.run', return_value=version_result):
        assert thclaws_validate(package)['status'] == 'failed'
        assert thclaws_validate(package, standalone_t0=True)['status'] == 'passed'
    wrong_revision = subprocess.CompletedProcess([], 0, 'thclaws 0.121.0\nrevision: test\n', '')
    with patch('forge.audit.shutil.which', return_value='test-thclaws'), patch(
            'forge.audit.subprocess.run', return_value=wrong_revision):
        assert thclaws_validate(package, standalone_t0=True)['status'] == 'failed'
    for pattern in ['dynamic', 'batch-fanout']:
        invalid = copy.deepcopy(spec)
        invalid['package_pattern'] = pattern
        rejects(lambda invalid=invalid: render(invalid))
    invalid = copy.deepcopy(spec)
    invalid['permissions']['tools'] = ['Write']  # T0 never admits a writer, whatever the spec claims
    rejects(lambda invalid=invalid: render(invalid))
    invalid = copy.deepcopy(spec)
    invalid['outputs'] *= 2
    rejects(lambda invalid=invalid: render(invalid))
    role_file = package / '.thclaws/agents/planner.md'
    original = role_file.read_text()
    role_file.write_text(original.replace('tools: Read', 'tools: Read, Bash'))
    assert exit_code(audit_package(package)) == 1, 'role permission drift accepted'
    role_file.write_text(original)
    rogue = package / '.thclaws/agents/rogue.md'
    rogue.write_text('---\nname: rogue\ntools: Write, Edit, Bash\n---\n', encoding='utf-8')
    rogue_report = audit_package(package)
    assert exit_code(rogue_report) == 1, 'added standalone role bypassed inventory audit'
    assert any(f['rule'] == 'inventory' and '.thclaws/agents/rogue.md' in f['message']
               for f in rogue_report['static_audit']['findings'])
    rogue.unlink()
    evil = package / '.thclaws/agent_workflow/evil.js'
    evil.write_text('globalThis.__wf_result = "{}";\n', encoding='utf-8')
    evil_report = audit_package(package)
    assert exit_code(evil_report) == 1, 'added standalone workflow bypassed inventory audit'
    assert any(f['rule'] == 'inventory' and '.thclaws/agent_workflow/evil.js' in f['message']
               for f in evil_report['static_audit']['findings'])
    evil.unlink()
    instructions = package / 'AGENTS.md'
    original_instructions = instructions.read_text()
    instructions.write_text(original_instructions + '\nOperator hardening that role snapshots would not see.\n')
    stale_report = audit_package(package)
    assert exit_code(stale_report) == 1, 'standalone AGENTS.md customization must require regeneration'
    assert any(f['rule'] == 'drift' and 'AGENTS.md' in f['message']
               for f in stale_report['static_audit']['findings'])
    instructions.write_text(original_instructions)
    instructions.write_text(original_instructions.replace('WorkflowRun', 'UnavailableRun'))
    assert exit_code(audit_package(package)) == 1
    instructions.write_text(original_instructions)
    value = {'text': 'Hello world'}
    suffix = '\n\n[workflow: 3 subagent turn(s), 12 in / 9 out tokens]\n'
    assert workflow_value(json.dumps(value) + suffix) == value
    for bad in [json.dumps(value), 'prose\n' + json.dumps(value) + suffix, '{"text":1,"text":2}' + suffix]:
        rejects(lambda bad=bad: workflow_value(bad))
    case = spec['evaluation']['golden_cases'][0]
    assert validate_value(spec, case, value) == value
    rejects(lambda: validate_value(spec, case, {'ok': True, 'steps': 1}))
    ambiguous = copy.deepcopy(spec)
    ambiguous['refusal']['schema'] = ambiguous['outputs'][0]['schema']
    rejects(lambda: validate_value(ambiguous, case, value))
    with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
        report, code = live_audit(package)
    assert code == 2 and report['audit']['live'] == 'skipped'
    write_report(package, report)
    print('M9 offline PASS: deterministic generation, roles, drift, result branches, parser, missing-key skip')
    return package


class Provider(BaseHTTPRequestHandler):
    """Scripted local provider drives real thClaws invocations, never a replacement runtime."""
    records: ClassVar[list] = []
    action = None
    wrong_branch = False

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.records.append(body)
        tool_messages = [m for m in body['messages'] if m['role'] == 'tool']
        if self.action and not tool_messages:
            name, arguments = self.action
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'probe-call', 'type': 'function',
                     'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
            finish = 'tool_calls'
        else:
            prompt = next(m['content'] for m in reversed(body['messages']) if m['role'] == 'user')
            value = {'text': 'probe'}
            if 'original input: ' in prompt.lower():
                envelope = json.JSONDecoder().raw_decode(prompt.split('input: ', 1)[1])[0]
                text = envelope.get('request', {}).get('text')
                value = ({'text': text} if isinstance(text, str) and text == 'Hello world' else
                         {'refused': True, 'reason': 'fixture refusal', 'handoff': 'owner'})
            if self.wrong_branch:
                value = {'ok': True, 'steps': 3}
            delta = {'role': 'assistant', 'content': json.dumps(value)}
            finish = 'stop'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for chunk, end in [(delta, None), ({}, finish)]:
            self.wfile.write(('data: ' + json.dumps({'id': 'probe', 'object': 'chat.completion.chunk',
                'model': 'm9-probe', 'choices': [{'index': 0, 'delta': chunk, 'finish_reason': end}]}) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')


def native(base, spec, binary, *, write_boundary=False):
    version = subprocess.run([binary, '--version'], capture_output=True, text=True, check=True, timeout=30)
    print(version.stdout.strip())
    if not write_boundary:
        lines = [line.strip() for line in version.stdout.splitlines() if line.strip()]
        runtime_version = lines[0].removeprefix('thclaws ') if lines else ''
        revision = next((line.split(':', 1)[1].strip().split()[0]
                         for line in lines[1:] if line.startswith('revision:') and ':' in line
                         and line.split(':', 1)[1].strip()), None)
        assert revision in STANDALONE_THCLAWS_COMPATIBILITY.get(runtime_version, frozenset()), (
            'inspect DESIGN before changing standalone compatibility'
        )
    upstream = base / 'upstream'
    subprocess.run([binary, 'agent', 'new', str(upstream), '--pattern', 'static-pipeline'],
                   capture_output=True, check=True, timeout=30)
    expected = {'.thclaws/agent_workflow/run.js', *[f'.thclaws/agents/{r}.md' for r in ['planner','worker','verifier']]}
    assert expected <= snapshot(upstream).keys()
    with ThreadingHTTPServer(('127.0.0.1', 0), Provider) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = f'http://127.0.0.1:{server.server_port}/v1'
            with patch.dict(os.environ, {'OPENAI_COMPAT_API_KEY': 'local-probe-only',
                                         'OPENAI_COMPAT_BASE_URL': endpoint, 'THCLAWS_BIN': binary}):
                probe_spec = copy.deepcopy(spec)
                probe_spec['model']['id'] = 'oai/m9-probe'
                package = base / 'native'
                generate(probe_spec, package)
                if not write_boundary:
                    report, code = live_audit(package, key_env='OPENAI_COMPAT_API_KEY', timeout=40)
                    assert code == 0, report['live_audit']
                    write_report(package, report)
                    assert len(Provider.records) == 12, 'expected three roles for each of four cases'
                    assert all({t['function']['name'] for t in r.get('tools', [])} == {'Read'} for r in Provider.records)
                    assert all(c['events'][0]['event'] == 'workflow_result' for c in report['live_audit']['cases'])
                    print('M9 T0 native PASS: three-role CLI pipeline, four scripted branches, report validation')
                    Provider.wrong_branch = True
                    failed, failed_code = live_audit(package, key_env='OPENAI_COMPAT_API_KEY', timeout=40)
                    assert failed_code == 1 and failed['package_status'] != 'candidate'
                    Provider.wrong_branch = False
                work = package
                if write_boundary:
                    # Intentionally unsupported native role, used only to probe the upstream blocker.
                    # Do not bypass the production generator's T0-only guard or claim this package passed audit.
                    role = work / '.thclaws/agents/worker.md'
                    role.write_text(role.read_text().replace('tools: Read', 'tools: Read, Write\nwritePaths: output/**'))
                (work / 'output').mkdir()
                home = base / 'probe-home'
                home.mkdir()
                config = base / 'probe-settings.json'
                config.write_text(json.dumps({'model': 'oai/m9-probe', 'browserEnabled': False}))
                env = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(home),
                       'XDG_CONFIG_HOME': str(home / '.config'), 'THCLAWS_CONFIG': str(config),
                       'OPENAI_COMPAT_API_KEY': 'local-probe-only', 'OPENAI_COMPAT_BASE_URL': endpoint}
                probes = ([('worker', 'Write', 'output/allowed.txt', True),
                           ('worker', 'Write', 'outside.txt', False),
                           ('worker', 'Write', 'output/../traversal.txt', False),
                           ('worker', 'Write', 'output/link/escaped.txt', False),
                           ('worker', 'Write', 'output/dangling', False)] if write_boundary else
                          [(role, tool, f'{role}-{tool}.txt', False)
                           for role in ('planner', 'worker', 'verifier') for tool in ('Write', 'Edit', 'Bash')])
                if write_boundary:
                    (work / 'outside-dir').mkdir()
                    (work / 'output/link').symlink_to(work / 'outside-dir', target_is_directory=True)
                    (work / 'output/dangling').symlink_to(work / 'outside-dir/dangling.txt')
                boundary_failed = False
                for role, tool, path, allowed in probes:
                    Provider.records.clear()
                    Provider.action = (tool, {'command': f'touch {path}'} if tool == 'Bash' else
                                       {'path': path, 'content': 'native probe'})
                    script = work / 'probe.js'
                    script.write_text('const r = await thclaws.subagent({agent: ' + json.dumps(role) +
                                      ', prompt: "permission probe"}); globalThis.__wf_result = JSON.stringify(r);')
                    workflow_process(binary, work, env, {}, 40, 'probe.js')
                    replies = [m['content'] for r in Provider.records for m in r['messages'] if m['role'] == 'tool']
                    assert replies, 'provider never received a tool result; absence alone is not denial evidence'
                    exists = (work / path).exists()
                    if exists and not allowed and write_boundary:
                        boundary_failed = True
                        print(f'M9 T1 BLOCKED: {path} created {(work / path).resolve().relative_to(work)} outside output')
                        continue
                    assert exists == allowed, (role, tool, path, replies)
                    if not allowed:
                        assert any('denied' in str(r).lower() or 'unknown tool' in str(r).lower()
                                   or 'not found' in str(r).lower() for r in replies), replies
                    print(f'M9 native PASS: {role} {tool} {path}: {"allowed" if allowed else "denied"}')
                if write_boundary:
                    print('M9 T1 write-boundary ' + ('FAIL (this binary must not certify T1)' if boundary_failed else
                                                   'PASS (diagnostic only; T1 certification is check_m14_standalone_t1.py)'))
                    return 1 if boundary_failed else 0
                Provider.action = None
        finally:
            Provider.action = None
            Provider.wrong_branch = False
            Provider.records.clear()
            server.shutdown()
            thread.join()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--write-boundary', action='store_true',
                        help='upstream writePaths diagnostic on a hand-edited role (for assessing a new binary); the shipped T1 gate is check_m14_standalone_t1.py')
    parser.add_argument('--model', help='explicit provider model for the live fixture; source fixture remains unchanged')
    parser.add_argument('--provider-key-env', default='OPENAI_API_KEY')
    parser.add_argument('--timeout', type=float, default=180)
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix='forge-m9-') as directory:
            base = Path(directory).resolve()
            spec = load_spec(ROOT / 'fixtures/text-reviewer-standalone/spec.yaml')
            binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
            if args.write_boundary:
                if not binary:
                    print('M9 T1 SKIP: thclaws absent; write-boundary diagnostic not run (exit 2)')
                    return 2
                return native(base, spec, binary, write_boundary=True)
            package = offline(base, spec)
            print('M9 scope: standalone T0. T1 is certified by check_m14_standalone_t1.py; --write-boundary stays an upstream diagnostic.')
            if not binary:
                print('M9 SKIP: thclaws absent; native validation/runtime and provider live checks not run (exit 2)')
                return 2
            native(base, spec, binary)
            if args.model:
                spec['model']['id'] = args.model
                package = base / 'live'
                generate(spec, package)
            report, code = live_audit(package, key_env=args.provider_key_env, timeout=args.timeout)
            write_report(package, report)
            print(f'M9 provider { ["PASS", "FAIL", "SKIP"][code] }: {report["live_audit"]["detail"]}')
            for case in report['live_audit']['cases']:
                print(f'  {case["id"]}: {case["status"]} {case["detail"]}')
            return code
    except (AssertionError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f'M9 FAIL: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
