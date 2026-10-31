#!/usr/bin/env python3
"""M19 gate: deterministic security audit, shippable promotion rule and report schema constraints.

Exit 0 = offline and native checks passed; 1 = failure; 2 = offline passed but thclaws is absent
or the baseline binary has no desktop --serve support. The provider is scripted on loopback only.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

try:
    from jsonschema import Draft202012Validator, ValidationError

    from forge.audit import audit_package, write_report
    from forge.generate import REPORT_SCHEMA_PATH, THCLAWS_BASELINE, generate, json_text
    from forge.live_test import live_audit
    from forge.pack_test import test_pack
    from forge.security import security_audit
    from forge.spec import load_spec

    _m15 = importlib.util.spec_from_file_location('check_m15', ROOT / 'scripts/check_m15_tool_allowlist.py')
    M15 = importlib.util.module_from_spec(_m15)
    _m15.loader.exec_module(M15)
except ModuleNotFoundError:
    print('M19 FAIL: install project dependencies from pyproject.toml first')
    raise SystemExit(1)

ECHO = ROOT / 'fixtures/packs/echo'
IGNORE = shutil.ignore_patterns('pack-conformance.json', '__pycache__')
VALIDATOR = Draft202012Validator(json.loads(REPORT_SCHEMA_PATH.read_text(encoding='utf-8')))
CLI = [sys.executable, '-m', 'forge.cli', 'audit']


def snapshot(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in sorted(folder.rglob('*')) if p.is_file()}


def expect(report, rule, needle, *, layer='security_audit', severity='error'):
    findings = report[layer]['findings']
    assert any(f['rule'] == rule and f['severity'] == severity and needle in f['message']
               for f in findings), f'expected {layer} {severity} {rule}: {needle}; got {findings}'


def checked(package, packs):
    report = audit_package(package, packs_dir=packs, strict=True, thclaws=False)
    previous = report['package_status']
    security_audit(package, report, packs_dir=packs)
    VALIDATOR.validate(report)
    assert report['package_status'] == previous, report
    return report


def synthetic_live(report, spec):
    result = copy.deepcopy(report)
    result['audit'].update(manifest='passed', live='passed')
    result['static_audit']['thclaws_validate']['status'] = 'passed'
    result['package_status'] = 'candidate'
    result['live_audit'] = {
        'status': 'passed', 'detail': 'synthetic',
        'cases': [{'id': c['id'], 'expect': c['expect'], 'status': 'passed', 'detail': '', 'events': []}
                  for c in spec['evaluation']['golden_cases']],
        'daemon_servers': [], 'called_tools': ['echo__reverse'], 'provenance': {},
    }
    VALIDATOR.validate(result)
    return result


def schema_checks(report, spec):
    live = synthetic_live(report, spec)
    invalid = []
    no_live = copy.deepcopy(report)
    no_live['package_status'] = 'shippable'
    invalid.append(('shippable without live', no_live))
    no_security = copy.deepcopy(live)
    no_security['package_status'] = 'shippable'
    no_security['audit']['security'] = 'not_run'
    del no_security['security_audit']
    invalid.append(('shippable without security', no_security))
    not_strict = copy.deepcopy(live)
    not_strict['package_status'] = 'shippable'
    not_strict['static_audit']['strict'] = False
    invalid.append(('shippable without strict', not_strict))
    findings = copy.deepcopy(report)
    findings['security_audit']['findings'] = [
        {'rule': 'secrets', 'severity': 'error', 'message': 'synthetic'}]
    invalid.append(('security passed with findings', findings))
    not_run = copy.deepcopy(report)
    not_run['audit']['security'] = 'not_run'
    invalid.append(('security not_run with block', not_run))
    for name, fake in invalid:
        try:
            VALIDATOR.validate(fake)
        except ValidationError:
            continue
        raise AssertionError(f'schema accepted {name}')
    return live


def run(argv, env, cwd, code, *needles):
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    output = result.stdout + result.stderr
    assert result.returncode == code, f'{argv}: expected exit {code}, got {result.returncode}\n{output}'
    for needle in needles:
        assert needle in result.stdout, f'{argv}: missing {needle!r}\n{output}'
    return result


def stored(package):
    report = json.loads((package / 'builder-build-report.json').read_text(encoding='utf-8'))
    VALIDATOR.validate(report)
    return report


def offline(base, packs, packs_unproven):
    spec = M15.probe_spec()
    package = base / 'package'
    generate(spec, package, packs_dir=packs)
    before = snapshot(package)
    report = checked(package, packs)
    assert report['security_audit']['status'] == 'passed', report
    assert report['security_audit']['findings'] == [], report
    assert report['security_audit']['scanned_files'] > 0, report
    assert report['audit']['security'] == 'passed' and report['package_status'] == 'unverified', report
    assert json_text(checked(package, packs)) == json_text(report), 'security audit is not deterministic'
    assert snapshot(package) == before, 'security audit wrote to the package'

    for name, relative, content, needle in (
        ('openai', 'AGENTS.md', '\nOPENAI_API_KEY=sk-' + 'a' * 24 + '\n', 'openai_key'),
        ('private', f'.thclaws/skills/{spec["identity"]["name"]}/SKILL.md',
         '\n-----BEGIN RSA PRIVATE KEY-----\n', 'private_key'),
        ('path', '.thclaws/scripts/client_secret.py', '# sensitive path probe\n', 'sensitive path'),
    ):
        planted = base / name
        shutil.copytree(package, planted)
        with (planted / relative).open('a', encoding='utf-8') as stream:
            stream.write(content)
        failed = checked(planted, packs)
        expect(failed, 'secrets', needle)
        secrets = [f for f in failed['security_audit']['findings'] if f['rule'] == 'secrets']
        assert len(secrets) == 1 and relative + ':' in secrets[0]['message'], secrets
        assert 'sk-aaaa' not in json_text(failed['security_audit']), 'finding leaked the secret value'
        assert failed['audit']['security'] == 'failed', failed
        if name == 'path':
            expect(failed, 'inventory', relative, layer='static_audit')
            expect(failed, 'strict', '--strict')
            assert failed['audit']['static'] == 'failed', failed

    unproven = base / 'unproven'
    generate(spec, unproven, packs_dir=packs_unproven)
    failed = checked(unproven, packs_unproven)
    expect(failed, 'packs', 'assets_bundled')
    assert failed['audit']['security'] == 'failed', failed
    unknown = copy.deepcopy(spec)
    unknown['permissions']['tools'].append('NotATool')
    generate(unknown, base / 'unknown', packs_dir=packs)
    failed = checked(base / 'unknown', packs)
    expect(failed, 'tools', 'NotATool', layer='static_audit', severity='warning')
    expect(failed, 'strict', '--strict')
    assert failed['audit']['static'] == 'failed', failed

    egress = load_spec(ROOT / 'fixtures/publisher/spec.yaml')
    egress['capabilities'] = [{'pack': 'echo', 'params': {}}]
    egress['permissions'].update(tier='T2', tools=['Read', 'echo__echo', 'echo__reverse'],
                                 shell='none', network='allowlist', write_scope='workspace')
    egress['env'] = []
    egress_path = base / 'egress.json'
    egress_path.write_text(json_text(egress), encoding='utf-8')
    try:
        egress = load_spec(egress_path)
    except ValueError as exc:
        assert 'T2 requires atlas-workflow with a human_gate' in str(exc), str(exc)
        print(f'M19 egress: M1 rejected the T2 atlas-workflow probe: {exc}')
    else:
        try:
            generate(egress, base / 'egress', packs_dir=packs)
        except ValueError as exc:
            assert 'allowlist' in str(exc) or 'T2' in str(exc), str(exc)
            print(f'M19 egress: M3/generator rejected the spec: {exc}')
        else:
            expect(checked(base / 'egress', packs), 'egress', 'no pack declares hosts')
            print('M19 egress: exercised on a T2 atlas-workflow probe')

    live = schema_checks(report, spec)
    security_audit(package, live, packs_dir=packs)
    assert live['package_status'] == 'shippable', live
    VALIDATOR.validate(live)
    skipped = synthetic_live(report, spec)
    skipped['audit']['live'] = 'skipped'
    skipped['live_audit']['status'] = 'skipped'
    skipped['package_status'] = 'draft'
    security_audit(package, skipped, packs_dir=packs)
    assert skipped['package_status'] == 'draft', skipped
    assert 'live evidence' in skipped['security_audit']['detail'], skipped
    VALIDATOR.validate(skipped)
    print('M19 synthetic PASS: Python API promotion rule only; this is not live evidence of a package')
    return spec, package


class Provider(BaseHTTPRequestHandler):
    """Golden-case responses with one real echo__reverse tool call in the normal case."""
    records: ClassVar[list] = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.records.append(body)
        messages = body['messages']
        prompt = next(m['content'] for m in reversed(messages) if m['role'] == 'user')
        normal = ('"operation": "reverse"' in prompt and '"text": "hello"' in prompt
                  and '"unexpected"' not in prompt)
        if normal and not any(m['role'] == 'tool' for m in messages):
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'm19-call', 'type': 'function',
                     'function': {'name': 'echo__reverse', 'arguments': json.dumps({'text': 'hello'})}}]}
            finish = 'tool_calls'
        else:
            value = ({'operation': 'reverse', 'text': 'olleh'} if normal
                     else {'refused': True, 'reason': 'm19 probe'})
            delta, finish = {'role': 'assistant', 'content': json.dumps(value)}, 'stop'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for chunk, end in ((delta, None), ({}, finish)):
            data = {'id': 'm19-probe', 'object': 'chat.completion.chunk', 'model': 'm19-probe',
                    'choices': [{'index': 0, 'delta': chunk, 'finish_reason': end}]}
            self.wfile.write(('data: ' + json.dumps(data) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')


def cli_runtime(base, packs):
    """Audit CLI has no packs-dir option: stage unchanged runtime files beside the temporary echo catalog."""
    runtime = base / 'runtime'
    runtime.mkdir()
    for name in ('forge', 'templates', 'patterns'):
        shutil.copytree(ROOT / name, runtime / name, ignore=shutil.ignore_patterns('__pycache__'))
    for source in ROOT.glob('*.schema.json'):
        shutil.copy2(source, runtime / source.name)
    shutil.copytree(packs, runtime / 'packs')
    return runtime


def cli_offline(base, package, runtime):
    # An empty PATH guarantees no thclaws, independently of an operator's installed binaries.
    empty_path = base / 'empty-bin'
    empty_path.mkdir()
    env = {**os.environ, 'PATH': str(empty_path), 'PYTHONPATH': str(runtime), 'PYTHONDONTWRITEBYTECODE': '1'}
    env.pop('THCLAWS_BIN', None)
    run([*CLI, str(package), '--security'], env, runtime, 2,
        'security: passed', 'package_status=unverified (SKIP)')
    run([sys.executable, str(package / 'studio.py'), '--security'], env, runtime, 2,
        'security: passed', 'package_status=unverified (SKIP)')
    assert stored(package)['audit']['security'] == 'passed'
    print('M19 offline PASS: conformant/unproven echo, deterministic non-writing audit, '
          'secrets/content/paths, '
          'strict warnings, egress semantics, five fake reports, synthetic promotion, CLI and studio exit 2')


def native(base, spec, packs, runtime, binary, timeout):
    version = M15.binary_pair(binary)
    assert version == THCLAWS_BASELINE, (
        f'thclaws {version} is not the {THCLAWS_BASELINE} baseline; re-check DESIGN §4 first')
    probe = copy.deepcopy(spec)
    probe['model']['id'] = 'oai/m19-probe'
    package = base / 'native'
    generate(probe, package, packs_dir=packs)
    env = {**os.environ, 'THCLAWS_BIN': binary, 'PYTHONPATH': str(runtime), 'PYTHONDONTWRITEBYTECODE': '1'}
    run([*CLI, str(package), '--security', '--write'], env, runtime, 0, 'package_status=draft (PASS)')
    assert stored(package)['audit']['security'] == 'passed'
    print('M19 native PASS: CLI --security --write -> draft; security passed; report schema valid')
    if not M15.desktop_build(binary):
        print('M19 SKIP: CLI-only thclaws build has no --serve; live promotion to shippable not run (exit 2)')
        return 2

    Provider.records.clear()
    with ThreadingHTTPServer(('127.0.0.1', 0), Provider) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        provider_env = {'THCLAWS_BIN': binary, 'OPENAI_COMPAT_API_KEY': 'local-probe-only',
                        'OPENAI_COMPAT_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1'}
        try:
            live = base / 'live'
            generate(probe, live, packs_dir=packs)
            with patch.dict(os.environ, provider_env):
                report, code = live_audit(live, packs_dir=packs, key_env='OPENAI_COMPAT_API_KEY',
                                          timeout=timeout, strict=True)
            assert code == 0 and report['package_status'] == 'candidate', report['live_audit']
            evidence = report['live_audit']
            assert 'echo__reverse' in evidence['called_tools'], evidence
            assert len(evidence['cases']) == 4, evidence
            assert all(c['status'] == 'passed' for c in evidence['cases']), evidence
            security_audit(live, report, packs_dir=packs)
            assert report['package_status'] == 'shippable' and report['audit']['security'] == 'passed', report
            write_report(live, report)
            runtime_version = report['static_audit']['thclaws_validate']['version']
            print(f'M19 native PASS: candidate -> shippable; 4/4 scripted golden cases; '
                  f'called={evidence["called_tools"]}; {runtime_version}')

            cli_live = base / 'cli-live'
            generate(probe, cli_live, packs_dir=packs)
            run([*CLI, str(cli_live), '--live', '--security', '--write', '--provider-key-env',
                 'OPENAI_COMPAT_API_KEY', '--timeout', str(timeout)], env | provider_env, runtime, 0,
                'live audit PASS:', 'package_status=shippable (PASS)')
            result = stored(cli_live)
            assert result['package_status'] == 'shippable' and result['audit']['security'] == 'passed', result
            print('M19 native PASS: CLI --live --security --write -> shippable; report schema valid')
        finally:
            print(f'M19 provider diagnostics: {len(Provider.records)} request(s); '
                  f'models={sorted({r.get("model", "") for r in Provider.records})}')
            server.shutdown()
            thread.join(timeout=5)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, default=180, help='seconds per live case (default: 180)')
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix='forge-m19-') as directory:
            base = Path(directory).resolve()
            # Keep helper temporary directories and bytecode inside this gate's temporary root.
            with patch.object(tempfile, 'tempdir', str(base)), patch.dict(os.environ, {
                'TMPDIR': str(base), 'PYTHONDONTWRITEBYTECODE': '1',
                'PYTHONPYCACHEPREFIX': str(base / 'pycache'),
            }):
                packs, packs_unproven = base / 'packs', base / 'packs_unproven'
                for target in (packs, packs_unproven):
                    shutil.copytree(ECHO, target / 'echo', ignore=IGNORE)
                evidence, code = test_pack('echo', packs, timeout=10)
                assert code == 0, f'echo conformance failed: {evidence}'
                spec, package = offline(base, packs, packs_unproven)
                runtime = cli_runtime(base, packs)
                cli_offline(base, package, runtime)
                binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
                if not binary:
                    print('M19 SKIP: thclaws absent; native validation and live shippable promotion '
                          'not run (exit 2)')
                    return 2
                return native(base, spec, packs, runtime, binary, args.timeout)
    except (AssertionError, ValueError, OSError, StopIteration, subprocess.SubprocessError) as exc:
        print(f'M19 FAIL: {type(exc).__name__}: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
