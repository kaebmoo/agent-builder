#!/usr/bin/env python3
"""M14: standalone T1 (write_scope output) — generated worker scoped by native writePaths, proven on the real package.

Exit 0 = offline checks, native write-boundary probes on the generator's own T1 package and the provider golden cases
all passed; 1 = failure (including a binary that is not a certified T1 pair: fail closed); 2 = offline passed but thclaws
or the provider key is absent, so the native or live part was explicitly skipped.

Scope (DESIGN §4 "Standalone T1 rollout"): the worker gets the declared Write/Edit under `writePaths: output/**`;
planner/verifier stay read-only; no role has Bash; MCP tools and runtime state are outside the boundary. The native part
uses `thclaws agent run` only (no `--serve`), so it also runs on a CLI-only build of thClaws.
"""
from __future__ import annotations

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
from forge.audit import audit_package, exit_code, thclaws_validate, write_report
from forge.generate import (
    STANDALONE_T1_THCLAWS_COMPATIBILITY,
    STANDALONE_THCLAWS_COMPATIBILITY,
    T1_WRITE_PATHS,
    generate,
    render,
    standalone_roles,
)
from forge.live_test import isolated_env, live_audit, tree_snapshot, workflow_process, written_files
from forge.spec import load_spec

FIXTURE = ROOT / 'fixtures/note-writer-standalone/spec.yaml'
T0_FIXTURE = ROOT / 'fixtures/text-reviewer-standalone/spec.yaml'
NOTE = 'output/note.md'
UNSUPPORTED_WRITERS = ('DocxCreate', 'PdfCreate', 'NotebookEdit', 'FetchImages')


def rejects(call, needle=None):
    try:
        call()
    except ValueError as exc:
        assert needle is None or needle in str(exc), (needle, str(exc))
        return
    raise AssertionError('expected a ValueError')


def frontmatter(package, role):
    return yaml.safe_load((package / f'.thclaws/agents/{role}.md').read_text(encoding='utf-8').split('---')[1])


def matrix(report, claim):
    return next(row for row in report['guarantee_matrix'] if row['claim'] == claim)


def offline(base, spec):
    package = base / 'package'
    generate(spec, package)
    generate(spec, base / 'repeat')
    assert tree_snapshot(package) == tree_snapshot(base / 'repeat'), 'T1 generation is not deterministic'
    assert not (package / 'output').exists(), 'output/ is created by the agent at run time, never shipped'
    roles = standalone_roles(spec, ROOT / 'packs')
    assert roles == {'planner': ['Read'], 'worker': ['Edit', 'Read', 'Write'], 'verifier': ['Read']}, roles
    worker = frontmatter(package, 'worker')
    assert worker['tools'].split(', ') == roles['worker'] and worker['writePaths'] == ', '.join(T1_WRITE_PATHS), worker
    for role in ('planner', 'verifier'):
        meta = frontmatter(package, role)
        assert 'writePaths' not in meta and not {'Write', 'Edit', 'Bash'} & set(meta['tools'].split(', ')), (role, meta)
    assert 'Write files only under output/' in (package / 'AGENTS.md').read_text(encoding='utf-8')
    report = audit_package(package, thclaws=False)
    assert exit_code(report) != 1, report['static_audit']
    assert matrix(report, 'output_write_scope')['status'] == 'Enforced'
    assert matrix(report, 'no_file_writes')['status'] == 'Not applicable'
    assert matrix(report, 'no_shell')['status'] == 'Enforced'
    assert 'MCP tools and runtime state are outside this boundary' in matrix(report, 'output_write_scope')['basis']
    t0_report = render(load_spec(T0_FIXTURE))[1]
    assert matrix(t0_report, 'output_write_scope')['status'] == 'Not applicable', 'T0 rows must not change'

    # Rejections: writers outside Write/Edit, a T1 without any writer, other patterns, other output contracts, T2.
    for tool in (*UNSUPPORTED_WRITERS, 'Bash', 'Task', 'WorkflowRun', 'MemoryWrite', 'KmsWrite', 'UnknownTool'):
        invalid = copy.deepcopy(spec)
        invalid['permissions']['tools'].append(tool)
        rejects(lambda invalid=invalid: render(invalid))
    readonly = copy.deepcopy(spec)
    readonly['permissions']['tools'] = ['Read']
    rejects(lambda: render(readonly), 'must declare Write or Edit')
    for pattern in ('dynamic', 'batch-fanout'):
        invalid = copy.deepcopy(spec)
        invalid['package_pattern'] = pattern
        rejects(lambda invalid=invalid: render(invalid))
    invalid = copy.deepcopy(spec)
    invalid['outputs'] *= 2
    rejects(lambda invalid=invalid: render(invalid))
    invalid = copy.deepcopy(spec)
    invalid['permissions'].update(tier='T2', write_scope='workspace')
    rejects(lambda invalid=invalid: render(invalid))
    edit_only = copy.deepcopy(spec)
    edit_only['permissions']['tools'] = ['Read', 'Edit']
    assert standalone_roles(edit_only, ROOT / 'packs')['worker'] == ['Edit', 'Read']

    # Drift: the boundary is part of the rendered contract, so weakening it in place fails the static audit.
    role_file = package / '.thclaws/agents/worker.md'
    original = role_file.read_text(encoding='utf-8')
    for mutation in (original.replace('writePaths: output/**', 'writePaths: **'),
                     original.replace('writePaths: output/**\n', ''),
                     original.replace('writePaths: output/**\n', '').replace('tools: Edit, Read, Write', 'tools: Bash, Edit, Read, Write')):
        role_file.write_text(mutation, encoding='utf-8')
        assert exit_code(audit_package(package, thclaws=False)) == 1, 'worker boundary drift accepted'
    role_file.write_text(original, encoding='utf-8')

    # Inventory: output/** is runtime output for T1 only; anything else undeclared is still a finding.
    (package / 'output').mkdir()
    (package / 'output/a.txt').write_text('agent output')
    clean = audit_package(package, thclaws=False)
    assert exit_code(clean) != 1 and not clean['static_audit']['findings'], clean['static_audit']
    (package / 'scratch.txt').write_text('stray')
    assert any(f['rule'] == 'inventory' and 'scratch.txt' in f['message']
               for f in audit_package(package, thclaws=False)['static_audit']['findings'])
    (package / 'scratch.txt').unlink()
    t0 = base / 't0'
    generate(load_spec(T0_FIXTURE), t0)
    (t0 / 'output').mkdir()
    (t0 / 'output/a.txt').write_text('not allowed for T0')
    assert any(f['rule'] == 'inventory' and 'output/a.txt' in f['message']
               for f in audit_package(t0, thclaws=False)['static_audit']['findings']), 'output/ must stay a finding for T0'

    # Certification pairs: T1 only on 0.138.0/d452ece and 0.146.0/9f69995 (M17); T0 keeps its five pairs.
    assert STANDALONE_T1_THCLAWS_COMPATIBILITY == {'0.138.0': frozenset({'d452ece'}), '0.146.0': frozenset({'9f69995'})}
    assert len(STANDALONE_THCLAWS_COMPATIBILITY) == 5 and '0.129.0' in STANDALONE_THCLAWS_COMPATIBILITY
    for version_text, tier, expected in (('thclaws 0.138.0\nrevision: d452ece\n', 'T1', 'passed'),
                                         ('thclaws 0.146.0\nrevision: 9f69995\n', 'T1', 'passed'),
                                         ('thclaws 0.146.0\nrevision: 492bfb2\n', 'T1', 'failed'),
                                         ('thclaws 0.129.0\nrevision: 7a27129\n', 'T1', 'failed'),
                                         ('thclaws 0.129.0\nrevision: 7a27129\n', 'T0', 'passed')):
        completed = subprocess.CompletedProcess([], 0, version_text, '')
        with patch('forge.audit.shutil.which', return_value='test-thclaws'), patch('forge.audit.subprocess.run', return_value=completed):
            assert thclaws_validate(package, standalone_tier=tier)['status'] == expected, (version_text, tier)
    legacy = copy.deepcopy(spec)
    legacy['identity']['thclaws_min_version'] = '>=0.116.0'
    assert render(legacy)[1]['declared_permissions']['tier'] == 'T1'

    # Live-audit tree diff helper: files outside output/ are reported separately from files inside it.
    before = tree_snapshot(package)
    (package / 'output/b.txt').write_text('new')
    (package / 'leak.txt').write_text('new')
    (package / '.thclaws/state').mkdir(parents=True, exist_ok=True)
    (package / '.thclaws/state/run.log').write_text('state is ignored')
    inside, outside = written_files(before, tree_snapshot(package))
    assert inside == ['output/b.txt'] and outside == ['leak.txt'], (inside, outside)
    (package / 'leak.txt').unlink()
    (package / 'output/b.txt').unlink()
    shutil.rmtree(package / '.thclaws/state')
    print('M14 offline PASS: deterministic T1 package, worker writePaths output/**, read-only planner/verifier, matrix rows, '
          'rejections, boundary drift, T1-only output/ inventory, tier-specific certification pairs, tree-diff helper')
    return package


class Provider(BaseHTTPRequestHandler):
    """Scripted local provider: real thClaws, scripted model. Drives the T1 pipeline and the write probes."""
    records: ClassVar[list] = []
    action = None  # (tool, arguments) forced on the first turn of a probe

    def log_message(self, *args):
        pass

    @staticmethod
    def role(body):
        system = '\n'.join(m['content'] for m in body['messages'] if m['role'] == 'system' and isinstance(m.get('content'), str))
        for name in ('planner', 'worker', 'verifier'):
            if f'You are the {name} subagent' in system:
                return name
        return 'unknown'

    @staticmethod
    def envelope(prompt):
        try:
            return json.JSONDecoder().raw_decode(prompt.split('input: ', 1)[1])[0]
        except (IndexError, ValueError):
            return {}

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.records.append(body)
        tool_messages = [m for m in body['messages'] if m['role'] == 'tool']
        prompt = next(m['content'] for m in reversed(body['messages']) if m['role'] == 'user')
        role = self.role(body)
        envelope = self.envelope(prompt)
        text = envelope.get('request', {}).get('text') if isinstance(envelope.get('request'), dict) else None
        normal = isinstance(text, str) and text == 'Hello world'
        if self.action and not tool_messages:
            name, arguments = self.action
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'probe-call', 'type': 'function',
                     'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
            finish = 'tool_calls'
        elif role == 'worker' and normal and not tool_messages:
            # The real write: the worker must create output/note.md through the scoped Write tool.
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'write-note', 'type': 'function',
                     'function': {'name': 'Write', 'arguments': json.dumps({'path': NOTE, 'content': text + '\n'})}}]}
            finish = 'tool_calls'
        else:
            if role == 'planner':
                value = {'plan': 'write the text to output/note.md' if normal else 'refuse'}
            elif normal:  # worker (after its Write) and verifier both answer the result branch for the normal case
                value = {'path': NOTE, 'chars': len(text + '\n')}
            else:
                value = {'refused': True, 'reason': 'fixture refusal', 'handoff': 'owner'}
            delta = {'role': 'assistant', 'content': json.dumps(value)}
            finish = 'stop'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for chunk, end in [(delta, None), ({}, finish)]:
            self.wfile.write(('data: ' + json.dumps({'id': 'probe', 'object': 'chat.completion.chunk', 'model': 'm14-probe',
                                                     'choices': [{'index': 0, 'delta': chunk, 'finish_reason': end}]}) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')


def binary_pair(binary):
    version = subprocess.run([binary, '--version'], capture_output=True, text=True, check=True, timeout=30)
    print(version.stdout.strip())
    lines = [line.strip() for line in version.stdout.splitlines() if line.strip()]
    runtime = lines[0].removeprefix('thclaws ') if lines else ''
    revision = next((line.split(':', 1)[1].strip().split()[0] for line in lines[1:]
                     if line.startswith('revision:') and line.split(':', 1)[1].strip()), None)
    return runtime, revision


def native(base, spec, binary):
    runtime, revision = binary_pair(binary)
    assert revision in STANDALONE_T1_THCLAWS_COMPATIBILITY.get(runtime, frozenset()), (
        f'{runtime}/{revision} is not a certified T1 pair; re-check DESIGN §4 "Standalone T1 rollout" before widening')
    package = base / 'native'
    probe_spec = copy.deepcopy(spec)
    probe_spec['model']['id'] = 'oai/m14-probe'
    generate(probe_spec, package)
    with tempfile.TemporaryDirectory(prefix='m14-pycache-') as cache:
        validate = subprocess.run([binary, 'agent', 'validate', str(package)], capture_output=True, text=True,
                                  env={**os.environ, 'PYTHONPYCACHEPREFIX': cache}, timeout=120, check=False)
    output = validate.stdout + validate.stderr
    assert validate.returncode == 0 and 'validates' in output, output
    assert 'writePaths' not in output, 'validate warned about writePaths (Bash alongside the boundary?): ' + output
    with ThreadingHTTPServer(('127.0.0.1', 0), Provider) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = f'http://127.0.0.1:{server.server_port}/v1'
            home = base / 'probe-home'
            home.mkdir()
            config = base / 'probe-settings.json'
            config.write_text(json.dumps({'model': 'oai/m14-probe', 'browserEnabled': False}))
            env = isolated_env(home, config, OPENAI_COMPAT_API_KEY='local-probe-only', OPENAI_COMPAT_BASE_URL=endpoint)
            work = base / 'probes'
            shutil.copytree(package, work)
            (work / 'outside-dir').mkdir()
            (work / 'outside-existing.txt').write_text('pre-existing file outside output\n')
            (work / 'output').mkdir()
            (work / 'output/link').symlink_to(work / 'outside-dir', target_is_directory=True)
            (work / 'output/dangling').symlink_to(work / 'outside-dir/dangling.txt')
            def edit(path):
                return {'path': path, 'old_string': 'native probe', 'new_string': 'edited probe'}

            probes = [
                ('worker', 'Write', {'path': 'output/allowed.txt', 'content': 'native probe'}, 'output/allowed.txt', True),
                ('worker', 'Edit', edit('output/allowed.txt'), 'output/allowed.txt', True),
                ('worker', 'Write', {'path': 'outside.txt', 'content': 'native probe'}, 'outside.txt', False),
                ('worker', 'Write', {'path': 'output/../traversal.txt', 'content': 'native probe'}, 'traversal.txt', False),
                ('worker', 'Write', {'path': 'output/link/escaped.txt', 'content': 'native probe'}, 'outside-dir/escaped.txt', False),
                ('worker', 'Write', {'path': 'output/dangling', 'content': 'native probe'}, 'outside-dir/dangling.txt', False),
                ('worker', 'Edit', {'path': 'outside-existing.txt', 'old_string': 'pre-existing', 'new_string': 'edited'}, None, False),
                ('worker', 'Bash', {'command': 'touch output/by-shell.txt'}, 'output/by-shell.txt', False),
                ('planner', 'Write', {'path': 'output/planner.txt', 'content': 'native probe'}, 'output/planner.txt', False),
                ('planner', 'Edit', edit('output/allowed.txt'), None, False),
                ('planner', 'Bash', {'command': 'touch output/planner-shell.txt'}, 'output/planner-shell.txt', False),
                ('verifier', 'Write', {'path': 'output/verifier.txt', 'content': 'native probe'}, 'output/verifier.txt', False),
                ('verifier', 'Edit', edit('output/allowed.txt'), None, False),
                ('verifier', 'Bash', {'command': 'touch output/verifier-shell.txt'}, 'output/verifier-shell.txt', False),
            ]
            for role, tool, arguments, created, allowed in probes:
                Provider.records.clear()
                Provider.action = (tool, arguments)
                before = tree_snapshot(work)
                script = work / 'probe.js'
                script.write_text('const r = await thclaws.subagent({agent: ' + json.dumps(role) +
                                  ', prompt: "permission probe"}); globalThis.__wf_result = JSON.stringify(r);')
                workflow_process(binary, work, env, {}, 60, 'probe.js')
                replies = [m['content'] for r in Provider.records for m in r['messages'] if m['role'] == 'tool']
                assert replies, (role, tool, 'provider never received a tool result; absence alone is not denial evidence')
                after = tree_snapshot(work)
                changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k) and k != 'probe.js')
                if allowed:
                    assert created in changed and (work / created).is_file(), (role, tool, arguments, changed, replies)
                    if tool == 'Edit':
                        assert 'edited probe' in (work / created).read_text(), replies
                else:
                    assert not changed, (role, tool, arguments, 'wrote outside the boundary: ' + ', '.join(changed), replies)
                    if created is not None:
                        assert not (work / created).exists(), (role, tool, created)
                    joined = ' '.join(str(r) for r in replies).lower()
                    assert any(word in joined for word in ('denied', 'unknown tool', 'not found', 'not available')), (role, tool, replies)
                print(f'M14 native PASS: {role} {tool} {arguments.get("path", arguments.get("command"))}: {"allowed" if allowed else "denied"}')
            Provider.action = None

            # The full pipeline on a pristine copy per case: the worker writes output/note.md for the normal case only.
            with patch.dict(os.environ, {'OPENAI_COMPAT_API_KEY': 'local-probe-only', 'OPENAI_COMPAT_BASE_URL': endpoint, 'THCLAWS_BIN': binary}):
                Provider.records.clear()
                report, code = live_audit(package, key_env='OPENAI_COMPAT_API_KEY', timeout=60)
                assert code == 0, report['live_audit']
                write_report(package, report)
                assert report['package_status'] == 'candidate'
                cases = {c['id']: c for c in report['live_audit']['cases']}
                files = {cid: next(e['data'] for e in c['events'] if e['event'] == 'files_written') for cid, c in cases.items()}
                assert files['normal'] == {'output': [NOTE], 'outside_output': []}, files['normal']
                for cid in ('missing-input', 'refusal', 'malformed'):
                    assert files[cid] == {'output': [], 'outside_output': []}, (cid, files[cid])
                assert all({t['function']['name'] for t in r.get('tools', [])} <= {'Read', 'Write', 'Edit'} for r in Provider.records)
                assert any({t['function']['name'] for t in r.get('tools', [])} == {'Read', 'Write', 'Edit'} for r in Provider.records), 'worker never saw its writers'
                assert any({t['function']['name'] for t in r.get('tools', [])} == {'Read'} for r in Provider.records), 'planner/verifier never saw a read-only registry'
                print('M14 native PASS: T1 pipeline wrote output/note.md for the normal case only, package is candidate under the scripted provider')
        finally:
            Provider.action = None
            Provider.records.clear()
            server.shutdown()
            thread.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--model', help='explicit provider model for the live fixture; the checked-in fixture is unchanged')
    parser.add_argument('--provider-key-env', default='OPENAI_API_KEY')
    parser.add_argument('--timeout', type=float, default=180)
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix='forge-m14-') as directory:
            base = Path(directory).resolve()
            spec = load_spec(FIXTURE)
            offline(base, spec)
            binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
            if not binary:
                print('M14 SKIP: thclaws absent; native write-boundary probes and provider live checks not run (exit 2)')
                return 2
            native(base, spec, binary)
            if not os.environ.get(args.provider_key_env, '').strip():
                print(f'M14 SKIP: no provider key in {args.provider_key_env}; live golden cases of the T1 fixture not run (exit 2)')
                return 2
            if args.model:
                spec['model']['id'] = args.model
            package = base / 'live'
            generate(spec, package)
            report, code = live_audit(package, key_env=args.provider_key_env, timeout=args.timeout)
            write_report(package, report)
            print(f'M14 provider {["PASS", "FAIL", "SKIP"][code]}: {report["live_audit"]["detail"]}')
            for case in report['live_audit']['cases']:
                written = next((e['data'] for e in case['events'] if e['event'] == 'files_written'), {})
                print(f'  {case["id"]}: {case["status"]} {case["detail"]} files={written}')
            return code
    except (AssertionError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f'M14 FAIL: {type(exc).__name__}: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
