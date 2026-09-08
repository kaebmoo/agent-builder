#!/usr/bin/env python3
"""M7b: publisher protocol, safe delivery attempts, T2 template and native draft audit."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import ssl
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.audit import audit_package, exit_code, static_findings
from forge.generate import generate, render
from forge.pack_test import RPC, setup_fixture, test_pack
from forge.packs import conformance, read_pack, validate_pack
from forge.spec import load_spec

NAME = 'publisher-email-sftp'
SOURCE = ROOT / 'packs' / NAME
EMAIL = {'subject': 'Synthetic report', 'body': 'Synthetic body', 'dry_run': False, 'idempotency_key': 'email'}
UPLOAD = {'filename': 'report.txt', 'dry_run': False, 'idempotency_key': 'file'}


def rejected(server, tool, args, reason):
    result = server.tool_call({'name': tool, 'arguments': args})
    assert result['isError'] and reason in result['content'][0]['text'], result


def server_checks(work, pack):
    fixture = work / 'fixture'
    fixture.mkdir()
    env, _ = setup_fixture(SOURCE, pack, fixture, 10)
    module = importlib.util.spec_from_file_location('publisher_server', SOURCE / 'scripts/server.py')
    server = importlib.util.module_from_spec(module)
    module.loader.exec_module(server)
    with patch.dict(os.environ, env):
        with patch.object(server, 'deliver', side_effect=AssertionError('dry run attempted delivery')):
            for tool, args in [('send_email', EMAIL), ('upload_file', UPLOAD)]:
                preview = server.publish(tool, {**args, 'dry_run': True})
                assert preview['status'] == 'dry_run'
            assert not list(Path(env['PUBLISHER_STATE_DIR']).iterdir()), 'dry run wrote state'
            for changes, reason in [({'dry_run': 'false'}, 'boolean'), ({'dry_run': 0}, 'boolean'),
                                    ({'idempotency_key': ''}, 'idempotency_key'),
                                    ({'subject': 'hello\nBcc: other@example.invalid'}, 'subject'),
                                    ({'recipient': 'other@example.invalid'}, 'arguments')]:
                rejected(server, 'send_email', {**EMAIL, **changes}, reason)
            for missing in ('dry_run', 'idempotency_key'):
                rejected(server, 'send_email', {k: v for k, v in EMAIL.items() if k != missing}, 'arguments')
            for name in ('../report.txt', '/tmp/report.txt', 'x\nput x y', 'x;rm', '-option'):
                rejected(server, 'upload_file', {**UPLOAD, 'filename': name}, 'filename')
            (fixture / 'input/link.txt').symlink_to(fixture / 'identity')
            rejected(server, 'upload_file', {**UPLOAD, 'filename': 'link.txt'}, 'artifact')
            os.mkfifo(fixture / 'input/pipe')
            rejected(server, 'upload_file', {**UPLOAD, 'filename': 'pipe'}, 'regular file')
            with (fixture / 'input/large').open('wb') as stream:
                stream.truncate(server.LIMIT + 1)
            rejected(server, 'upload_file', {**UPLOAD, 'filename': 'large'}, '10 MiB')

        with patch.object(server, 'deliver') as delivery:
            for tool, args in [('send_email', EMAIL), ('upload_file', UPLOAD)]:
                first = server.publish(tool, args)
                assert first['status'] == 'sent' and first == server.publish(tool, args)
            assert delivery.call_count == 2, 'retry sent twice or preview consumed the key'
            rejected(server, 'send_email', {**EMAIL, 'body': 'Changed'}, 'conflicts')
            rejected(server, 'upload_file', {**UPLOAD, 'idempotency_key': EMAIL['idempotency_key']}, 'conflicts')
            (fixture / 'input/report.txt').write_text('Changed artifact')
            rejected(server, 'upload_file', UPLOAD, 'conflicts')
            config = json.loads(env['PUBLISHER_CONFIG'])
            changed = copy.deepcopy(config)
            changed['smtp']['recipient'] = 'changed@example.invalid'
            with patch.dict(os.environ, {'PUBLISHER_CONFIG': json.dumps(changed)}):
                rejected(server, 'send_email', EMAIL, 'conflicts')
            assert delivery.call_count == 2

        with patch.object(server, 'deliver', side_effect=OSError('SYNTHETIC_PRIVATE_RESPONSE')) as delivery:
            uncertain = {**EMAIL, 'idempotency_key': 'uncertain'}
            rejected(server, 'send_email', uncertain, 'outcome unknown')
            rejected(server, 'send_email', uncertain, 'reconciliation')
            assert delivery.call_count == 1, 'unknown delivery retried'

        started, release = threading.Event(), threading.Event()

        def blocked_delivery(*args):
            started.set()
            assert release.wait(5)

        with patch.object(server, 'deliver', side_effect=blocked_delivery) as delivery:
            concurrent = {**EMAIL, 'idempotency_key': 'concurrent'}
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(server.publish, 'send_email', concurrent)
                try:
                    assert started.wait(5)
                    rejected(server, 'send_email', concurrent, 'reconciliation')
                finally:
                    release.set()
                assert future.result()['status'] == 'sent'
            assert delivery.call_count == 1, 'concurrent duplicate escaped reservation'

        # Exercise the real transport adapter functions with controlled boundaries, never real recipients.
        settings, data, _ = server.prepare('send_email', EMAIL)
        with patch.object(server.smtplib, 'SMTP_SSL') as smtp:
            smtp.return_value.__enter__.return_value.send_message.return_value = {}
            server.deliver('send_email', EMAIL, settings, data)
            context = smtp.call_args.kwargs['context']
            assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
            sent = smtp.return_value.__enter__.return_value.send_message.call_args
            assert sent.kwargs['to_addrs'] == [settings['recipient']]
            assert sent.args[0]['Subject'] == EMAIL['subject']
        settings, data, _ = server.prepare('upload_file', UPLOAD)

        def fake_sftp(argv, **kwargs):
            assert argv[0] == 'sftp' and '-oStrictHostKeyChecking=yes' in argv
            assert '-oBatchMode=yes' in argv and '-oIdentityAgent=none' in argv
            assert argv[1:3] == ['-F', '/dev/null'] and '-oProxyCommand=none' in argv
            assert '-oUserKnownHostsFile=' + settings['known_hosts'] in argv
            assert kwargs['check'] is True and not kwargs.get('shell') and kwargs['timeout'] == 60
            assert set(kwargs['env']) == {'PATH'}
            assert kwargs['input'] == 'put artifact /publications/report.txt\n'
            assert Path(kwargs['cwd'], 'artifact').read_bytes() == data

        with patch.object(server.subprocess, 'run', side_effect=fake_sftp):
            server.deliver('upload_file', UPLOAD, settings, data)

    # A NEW server process must replay completed attempts, and keep uncertain ones blocked.
    rpc = RPC([sys.executable, str(SOURCE / 'scripts/server.py')], fixture,
              {'PATH': os.environ.get('PATH', os.defpath), **env}, 5)
    try:
        rpc.call('initialize', {})
        replay = rpc.call('tools/call', {'name': 'send_email', 'arguments': EMAIL})
        assert not replay['isError'] and json.loads(replay['content'][0]['text'])['status'] == 'sent'
        result = rpc.call('tools/call', {'name': 'send_email', 'arguments': uncertain})
        assert result['isError'] and 'reconciliation' in result['content'][0]['text']
    finally:
        rpc.close()
    print('M7b PASS: dry run, argument/path rejection, persistent/concurrent deduplication and transport adapters')


def check(work):
    # Conformance evidence belongs in a temporary copy so this gate never dirties the source pack.
    packs = work / 'packs'
    folder = packs / NAME
    shutil.copytree(SOURCE, folder, ignore=shutil.ignore_patterns('pack-conformance.json', '__pycache__'))
    pack = read_pack(folder)
    server_checks(work, pack)
    evidence, code = test_pack(NAME, packs)
    assert code == 0 and conformance(folder, pack) == evidence, evidence
    repeat, code = test_pack(NAME, packs)
    assert code == 0 and repeat == evidence, 'conformance is not deterministic'

    spec = load_spec(ROOT / 'fixtures/publisher/spec.yaml')
    before = copy.deepcopy(spec)
    first, report, problems = render(spec, packs)
    second, repeated, repeated_problems = render(spec, packs)
    assert not problems and not repeated_problems and first == second and report == repeated
    assert spec == before and report['package_status'] == 'unverified'
    assert report['dependencies'][0]['status'] == 'conformant'
    config = json.loads(first['.thclaws/mcp.json'])['mcpServers'][NAME]
    assert config == {'command': 'python3', 'args': ['.thclaws/scripts/publisher-email-sftp--server.py']}
    assert not any(path.endswith('.db') or 'fixture/' in path for path in first)
    assert all('env' not in server for server in json.loads(first['.thclaws/mcp.json'])['mcpServers'].values())
    for tier in ('T0', 'T1'):
        invalid = copy.deepcopy(pack)
        invalid['min_tier'] = tier
        try:
            validate_pack(invalid, folder)
        except ValueError as exc:
            assert 'T2' in str(exc)
        else:
            raise AssertionError('mutating pack accepted below T2')
    lower = load_spec(ROOT / 'fixtures/invoice-reviewer/spec.yaml')
    lower['capabilities'] = spec['capabilities']
    lower['permissions']['tools'] = spec['permissions']['tools']
    lower['env'] = spec['env']
    try:
        render(lower, packs)
    except ValueError as exc:
        assert 'requires T2' in str(exc)
    else:
        raise AssertionError('publisher accepted below T2')
    for target in ('atlas-worker', 'thclaws-standalone'):
        invalid = copy.deepcopy(spec)
        invalid['target'] = target
        if target == 'thclaws-standalone':
            invalid.pop('routing')
            invalid['package_pattern'] = 'static-pipeline'
        try:
            render(invalid, packs)
        except ValueError as exc:
            assert 'T2' in str(exc)
        else:
            raise AssertionError('T2 accepted without an Atlas workflow')
    no_network = copy.deepcopy(spec)
    no_network['permissions']['network'] = 'none'
    assert any('required network' in error for error in render(no_network, packs)[2])

    t0_files, t0_report, _ = render(load_spec(ROOT / 'fixtures/invoice-reviewer/spec.yaml'), packs)
    assert 'atlas-node-template.json' not in t0_files
    t0 = {row['claim']: row['status'] for row in t0_report['guarantee_matrix']}
    t2 = {row['claim']: row['status'] for row in report['guarantee_matrix']}
    assert t0['human_approval'] == 'Not applicable' and t2['human_approval'] == 'Not verified'
    assert t0['no_network_builtins'] == 'Static check' and t2['no_network_builtins'] == 'Not applicable'
    assert t0['no_shell'] == t2['no_shell'] == 'Not guaranteed'
    template = json.loads(first['atlas-node-template.json'])
    requirements = template['deployment_requirements']
    assert requirements['isolated_daemon_required'] and not requirements['deployment_verified']
    graph, policy = template['workflow']['graph'], template['workflow']['policy']
    gate, worker = graph['nodes']
    assert graph['start'] == gate['id'] and gate['type'] == 'human_gate'
    assert graph['edges'] == [{'from': gate['id'], 'to': worker['id'],
                               'condition': {'type': 'human_selected', 'choice': 'approve'}}]
    for field in ('worker_id', 'workspace_id'):
        assert worker[field] == requirements[field] and worker[field].startswith('__OPERATOR_T2_')
        assert policy['allowed_' + field + 's'] == [worker[field]]
    assert worker['model'] == spec['model']['id'] and worker['output_format'] == 'json'

    package = work / 'package'
    generate(spec, package, packs_dir=packs)
    assert not static_findings(package, packs)[0]
    target = package / 'atlas-node-template.json'
    for mutation in ('gate', 'edge', 'isolation', 'binding'):
        changed = copy.deepcopy(template)
        if mutation == 'gate':
            changed['workflow']['graph']['nodes'].pop(0)
        elif mutation == 'edge':
            changed['workflow']['graph']['edges'][0]['condition'] = {'type': 'always'}
        elif mutation == 'isolation':
            changed['deployment_requirements']['isolated_daemon_required'] = False
        else:
            changed['workflow']['graph']['nodes'][1].pop('workspace_id')
        target.write_text(json.dumps(changed))
        findings, _ = static_findings(package, packs)
        assert any(f['rule'] == 'drift' and 'atlas-node-template.json' in f['message'] for f in findings)
    target.unlink()
    assert any(f['rule'] == 'inventory' for f in static_findings(package, packs)[0])
    target.write_bytes(first['atlas-node-template.json'])
    print('M7b PASS: conformance, deterministic generation, tier boundaries and audited approval/isolation template')
    with patch.dict(os.environ, {'THCLAWS_BIN': 'forge-no-such-binary'}):
        skipped = audit_package(package, packs_dir=packs)
        assert exit_code(skipped) == 2 and skipped['package_status'] == 'unverified'
    audited = audit_package(package, packs_dir=packs)
    result = exit_code(audited)
    if result == 2:
        print('M7b SKIP: thclaws not available; native validation not run, package remains unverified')
    elif result == 0:
        assert audited['package_status'] == 'draft'
        print('M7b PASS: native draft audit (' + audited['static_audit']['thclaws_validate']['version'] + ')')
    else:
        raise AssertionError(audited['static_audit'])
    return result


if __name__ == '__main__':
    try:
        with tempfile.TemporaryDirectory(prefix='forge-m7b-') as temporary:
            status = check(Path(temporary).resolve())
    except (AssertionError, ValueError, OSError) as exc:
        print(f'M7b FAIL: {exc}')
        status = 1
    raise SystemExit(status)
