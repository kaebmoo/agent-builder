#!/usr/bin/env python3
"""M22 gate: unverified-runtime opt-in, guarantee downgrades, promotion, schema and export rules.

Exit 0 = offline and native checks passed; 1 = failure (including a non-baseline real binary);
2 = offline passed but thclaws is absent. A wrapper fakes only --version; the reviewed baseline
performs validation and packing. No provider key, loopback server or real-provider live evidence.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

try:
    from jsonschema import Draft202012Validator, ValidationError

    from forge.atlas_export import bind, render_export
    from forge.audit import (
        RUNTIME_SENSITIVE_CLAIMS,
        audit_package,
        downgrade_runtime_rows,
        runtime_identity,
        write_report,
    )
    from forge.generate import (
        REPORT_SCHEMA_PATH,
        STANDALONE_THCLAWS_COMPATIBILITY,
        THCLAWS_BASELINE,
        generate,
        guarantee_matrix,
        json_text,
    )
    from forge.live_test import live_audit
    from forge.security import security_audit
    from forge.spec import load_spec
except ModuleNotFoundError:
    print('M22 FAIL: install project dependencies from pyproject.toml first')
    raise SystemExit(1)

FAKE_VERSION_TEXT = 'thclaws 0.999.0\nrevision: 0000000 (fake)\n'
FAKE = 'thclaws 0.999.0 revision: 0000000 (fake)'
FIXTURES = {'text-reviewer-standalone': {'tool_allowlist', 'no_shell'},
            'invoice-reviewer': {'tool_allowlist', 'no_shell'}}
T1_FIXTURE = 'note-writer-standalone'
PREFIX = f'Runtime {FAKE} not reviewed against DESIGN §4: '
CLI = [sys.executable, '-m', 'forge.cli']
VALIDATOR = Draft202012Validator(json.loads(REPORT_SCHEMA_PATH.read_text(encoding='utf-8')))
RUNTIME_DETAIL = f'runtime {FAKE} is not a verified baseline; shippable requires a verified runtime'


def cli_env(binary):
    env = {**os.environ, 'THCLAWS_BIN': str(binary), 'PYTHONDONTWRITEBYTECODE': '1'}
    env.pop('OPENAI_API_KEY', None)
    return env


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


def rejected(report, name):
    try:
        VALIDATOR.validate(report)
    except ValidationError:
        return
    raise AssertionError(f'schema accepted {name}')


def synthetic(report, runtime):
    result = copy.deepcopy(report)
    result['audit'].update(manifest='passed', live='passed', security='passed')
    result['static_audit']['strict'] = True
    result['static_audit']['thclaws_validate'].update(
        status='passed', version=FAKE, runtime=runtime, revision='0000000')
    result['live_audit'] = {
        'status': 'passed', 'detail': 'synthetic',
        'cases': [{'id': name, 'expect': expect, 'status': 'passed', 'detail': '', 'events': []}
                  for name, expect in (('normal', 'result'), ('missing', 'refusal'),
                                       ('refusal', 'refusal'), ('malformed', 'refusal'))],
        'daemon_servers': [], 'called_tools': [], 'provenance': {},
        'runtime': {'version': FAKE, 'revision': '0000000', 'verified': runtime == 'verified'},
    }
    result['security_audit'] = {'status': 'passed', 'strict': True, 'findings': [],
                                'scanned_files': 1, 'detail': 'synthetic'}
    result['package_status'] = 'shippable'
    return result


def candidate(report, runtime):
    result = synthetic(report, runtime)
    result['package_status'] = 'candidate'
    result['audit']['security'] = 'not_run'
    del result['security_audit']
    return result


def downgraded(before, after, expected):
    old = {row['claim']: row for row in before['guarantee_matrix']}
    new = {row['claim']: row for row in after['guarantee_matrix']}
    assert old.keys() == new.keys(), (old, new)
    changed = {claim for claim in old if old[claim] != new[claim]}
    assert changed == expected, (changed, expected)
    for claim in changed:
        assert new[claim] == {**old[claim], 'status': 'Declared', 'basis': PREFIX + old[claim]['basis']}
    assert downgrade_runtime_rows(after['guarantee_matrix'], FAKE) == after['guarantee_matrix']


def offline(base):
    claims = {'tool_allowlist', 'no_shell', 'output_write_scope', 'collect_files'}
    assert RUNTIME_SENSITIVE_CLAIMS == claims, RUNTIME_SENSITIVE_CLAIMS
    matrix = [{'claim': claim, 'status': 'Enforced', 'basis': f'old {claim}'}
              for claim in sorted(claims)] + [
        {'claim': claim, 'status': status, 'basis': f'old {claim}'}
        for claim, status in (('output_schema', 'Enforced'), ('no_file_writes', 'Not guaranteed'),
                              ('network_hosts', 'Declared'), ('human_approval', 'Not applicable'),
                              ('no_network_builtins', 'Static check'), ('skill_mcp_calls', 'No evidence'))]
    before = copy.deepcopy(matrix)
    reduced = downgrade_runtime_rows(matrix, FAKE)
    downgraded({'guarantee_matrix': matrix}, {'guarantee_matrix': reduced}, claims)
    assert matrix == before, 'downgrade mutated its input'

    for name in FIXTURES:
        spec = load_spec(ROOT / 'fixtures' / name / 'spec.yaml')
        assert spec['capabilities'] == [], spec
        pkg = base / 'offline' / name
        generate(spec, pkg)
        report = audit_package(pkg, thclaws=False)
        assert 'runtime' not in report['static_audit']['thclaws_validate'], report
        VALIDATOR.validate(report)
        opted_in = audit_package(pkg, thclaws=False, allow_unverified_runtime=True)
        assert json_text(opted_in) == json_text(report)

    # The last fixture is Atlas, so its report also exercises export without a binary.
    assert name == 'invoice-reviewer'
    unverified = synthetic(report, 'unverified')
    rejected(unverified, 'shippable on an unverified runtime')
    VALIDATOR.validate(synthetic(report, 'verified'))
    unverified['package_status'] = 'candidate'
    VALIDATOR.validate(unverified)
    skipped = copy.deepcopy(report)
    skipped['static_audit']['thclaws_validate']['runtime'] = 'unverified'
    rejected(skipped, 'skipped validation with runtime')

    cand = candidate(report, 'unverified')
    security_audit(pkg, cand)
    assert cand['package_status'] == 'candidate' and cand['audit']['security'] == 'passed', cand
    assert cand['security_audit']['detail'] == RUNTIME_DETAIL, cand
    VALIDATOR.validate(cand)
    verified = candidate(report, 'verified')
    security_audit(pkg, verified)
    assert verified['package_status'] == 'shippable', verified
    VALIDATOR.validate(verified)

    write_report(pkg, report)
    files = render_export(spec, stored(pkg), bind(
        spec, base_url='http://127.0.0.1:4317', workspace_dir='/srv/m22'), None, None)
    assert json.loads(files['atlas-register.json'])['deployment']['runtime'] is None
    for command in ('audit', 'live-test', 'export'):
        run([*CLI, command, '--help'], cli_env(os.environ.get('THCLAWS_BIN', 'thclaws')),
            ROOT, 0, '--allow-unverified-runtime')
    print('M22 offline PASS: pure/idempotent four-claim downgrade; no-binary audit unchanged; '
          'schema rejects unverified shippable and skipped runtime; synthetic promotion ceiling; '
          'export runtime null; audit/live-test/export flag help')


def native(base, binary):
    lines, runtime_version, revision = runtime_identity(binary)
    assert (lines and lines[0] == f'thclaws {THCLAWS_BASELINE}'
            and revision in STANDALONE_THCLAWS_COMPATIBILITY[THCLAWS_BASELINE]), (
        f'{lines[0] if lines else "<no output>"} revision {revision} is not the reviewed baseline; '
        're-check DESIGN §4 first')
    assert runtime_version == THCLAWS_BASELINE
    real = ' '.join(lines[:2])
    wrapper = base / 'bin' / 'thclaws'
    wrapper.parent.mkdir()
    fake_printf = FAKE_VERSION_TEXT.replace('\n', '\\n')
    wrapper.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then '
                       f"printf '{fake_printf}'; exit 0; fi\n"
                       f'exec "{binary}" "$@"\n', encoding='utf-8')
    wrapper.chmod(0o755)
    assert shutil.which(str(wrapper)) == str(wrapper)
    assert run([str(wrapper), '--version'], cli_env(wrapper), ROOT, 0).stdout == FAKE_VERSION_TEXT
    env = cli_env(wrapper)
    with patch.dict(os.environ, cli_env(binary), clear=True):
        for name, expected in FIXTURES.items():
            spec = load_spec(ROOT / 'fixtures' / name / 'spec.yaml')
            pkg = base / 'native' / name
            generate(spec, pkg)
            a = audit_package(pkg)
            b = audit_package(pkg, allow_unverified_runtime=True)
            assert json_text(a) == json_text(b), name
            validate = a['static_audit']['thclaws_validate']
            assert validate['runtime'] == 'verified' and validate['revision'] == revision, validate
            assert validate['version'] == real, validate
            assert a['guarantee_matrix'] == guarantee_matrix(spec), a
            VALIDATOR.validate(a)
            legacy = copy.deepcopy(a)
            del legacy['static_audit']['thclaws_validate']['runtime']
            del legacy['static_audit']['thclaws_validate']['revision']
            VALIDATOR.validate(legacy)
            write_report(pkg, a)

            with patch.dict(os.environ, env, clear=True):
                n = audit_package(pkg)
                validate = n['static_audit']['thclaws_validate']
                assert n['package_status'] == 'unverified' and n['audit']['manifest'] == 'failed', n
                assert validate['runtime'] == 'unverified' and validate['revision'] == '0000000', validate
                assert validate['version'] == FAKE and 'is not' in validate['detail'], validate
                assert n['guarantee_matrix'] == a['guarantee_matrix'], n
                VALIDATOR.validate(n)
                result = run([*CLI, 'audit', str(pkg)], env, ROOT, 1, 'package_status=unverified (FAIL)')
                assert 'on UNVERIFIED runtime' not in result.stdout, result.stdout

                f = audit_package(pkg, allow_unverified_runtime=True)
                assert f['package_status'] == 'draft' and f['audit']['manifest'] == 'passed', f
                assert f['static_audit']['thclaws_validate']['runtime'] == 'unverified', f
                downgraded(a, f, expected)
                VALIDATOR.validate(f)
                run([*CLI, 'audit', str(pkg), '--allow-unverified-runtime', '--write'], env, ROOT, 0,
                    f'thclaws agent validate: passed on UNVERIFIED runtime {FAKE} '
                    f'(baseline {THCLAWS_BASELINE}); runtime guarantees downgraded',
                    'package_status=draft (PASS) on UNVERIFIED runtime')
                assert json_text(stored(pkg)) == json_text(f), name

                run([*CLI, 'audit', str(pkg), '--allow-unverified-runtime', '--security'], env, ROOT, 0,
                    'security: passed', 'package_status=draft (PASS) on UNVERIFIED runtime')
                strict = audit_package(pkg, strict=True, allow_unverified_runtime=True)
                assert strict['package_status'] == 'draft' and not strict['static_audit']['findings'], strict
                cand = candidate(strict, 'unverified')
                cand['static_audit']['thclaws_validate'] = copy.deepcopy(
                    strict['static_audit']['thclaws_validate'])
                security_audit(pkg, cand)
                assert cand['package_status'] == 'candidate' and cand['audit']['security'] == 'passed', cand
                assert cand['security_audit']['detail'] == RUNTIME_DETAIL, cand
                VALIDATOR.validate(cand)
                cand['package_status'] = 'shippable'
                rejected(cand, 'native synthetic shippable on an unverified runtime')

                rep, code = live_audit(pkg, allow_unverified_runtime=True)
                assert code == 2, rep
                assert rep['live_audit']['runtime'] == {
                    'version': FAKE, 'revision': '0000000', 'verified': False}, rep
                assert 'no provider key' in rep['live_audit']['detail'], rep
                VALIDATOR.validate(rep)
                rep0, code0 = live_audit(pkg)
                assert code0 == 1 and 'blocked' in rep0['live_audit']['detail'], rep0
                assert rep0['live_audit']['runtime']['verified'] is False, rep0
                VALIDATOR.validate(rep0)
                run([*CLI, 'live-test', str(pkg), '--allow-unverified-runtime'], env, ROOT, 2,
                    'package_status=draft on UNVERIFIED runtime')
            restored = audit_package(pkg, allow_unverified_runtime=True)
            assert restored['guarantee_matrix'] == a['guarantee_matrix']
            print(f'M22 native PASS: {name}: wrapper {FAKE} -> unverified without flag, draft with flag, '
                  f'rows {sorted(expected)} Declared; baseline {real} unchanged')

        assert name == 'invoice-reviewer'
        export_args = [*CLI, 'export', str(pkg), '--base-url', 'http://127.0.0.1:4317',
                       '--workspace-dir', '/srv/m22']
        flag_out = base / 'export-flag'
        run([*export_args, '--out', str(flag_out), '--allow-unverified-runtime'], env, ROOT, 0,
            f'warning: UNVERIFIED runtime {FAKE} (baseline {THCLAWS_BASELINE})')
        register = json.loads((flag_out / 'atlas-register.json').read_text(encoding='utf-8'))
        assert register['deployment']['runtime'] == {
            'version': FAKE, 'revision': '0000000', 'verified': False, 'baseline': THCLAWS_BASELINE}, register
        archive = flag_out / register['package']['archive']
        with tarfile.open(archive) as packed:
            assert {m.name for m in packed.getmembers() if m.isfile()} == set(stored(pkg)['generated_files'])
        digest = archive.with_name(archive.name + '.sha256').read_text(encoding='utf-8').split()[0]
        assert digest == hashlib.sha256(archive.read_bytes()).hexdigest(), archive
        noflag_out = base / 'export-noflag'
        run([*export_args, '--out', str(noflag_out)], env, ROOT, 1,
            'records thclaws agent validate on an unverified runtime')
        assert not noflag_out.exists(), noflag_out
        write_report(pkg, a)
        run([*export_args, '--out', str(noflag_out)], env, ROOT, 1, 'is not the verified baseline')
        assert not noflag_out.exists(), noflag_out
        real_out = base / 'export-real'
        result = run([*export_args, '--out', str(real_out)], cli_env(binary), ROOT, 0)
        register = json.loads((real_out / 'atlas-register.json').read_text(encoding='utf-8'))
        assert register['deployment']['runtime'] == {
            'version': real, 'revision': revision, 'verified': True, 'baseline': THCLAWS_BASELINE}, register
        assert 'warning:' not in result.stdout, result.stdout
        print('M22 native PASS: export: wrapper + flag -> archive + deployment.runtime.verified=false '
              '+ warning; '
              'without flag refused; baseline -> verified=true')

        spec = load_spec(ROOT / 'fixtures' / T1_FIXTURE / 'spec.yaml')
        assert spec['capabilities'] == [], spec
        pkg = base / 'native' / T1_FIXTURE
        generate(spec, pkg)
        a1 = audit_package(pkg)
        assert a1['package_status'] == 'draft', a1
        with patch.dict(os.environ, env, clear=True):
            f1 = audit_package(pkg, allow_unverified_runtime=True)
        assert f1['package_status'] == 'draft', f1
        downgraded(a1, f1, {'tool_allowlist', 'no_shell', 'output_write_scope'})
        VALIDATOR.validate(f1)
        print(f'M22 native PASS: {T1_FIXTURE}: output_write_scope downgraded to Declared on the wrapper')
    print(f'M22 native PASS: all (a)-(f) checks on wrapper {FAKE} against baseline {real}; '
          'live evidence with a real provider not part of this gate')
    return 0


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix='forge-m22-') as directory:
            base = Path(directory).resolve()
            with patch.object(tempfile, 'tempdir', str(base)), patch.dict(os.environ, {
                'TMPDIR': str(base), 'PYTHONDONTWRITEBYTECODE': '1',
                'PYTHONPYCACHEPREFIX': str(base / 'pycache'),
            }):
                offline(base)
                binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
                if not binary:
                    print('M22 SKIP: thclaws absent; wrapper and baseline checks not run (exit 2)')
                    return 2
                return native(base, binary)
    except (AssertionError, ValueError, OSError, KeyError,
            subprocess.SubprocessError, tarfile.TarError) as exc:
        print(f'M22 FAIL: {type(exc).__name__}: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
