#!/usr/bin/env python3
"""M7a: pack v2, adversarial MCP conformance, SQL fixture and native draft promotion."""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import yaml

    from forge.audit import audit_package, exit_code
    from forge.generate import generate, render
    from forge.pack_test import RPC, test_pack
    from forge.packs import conformance, read_pack, validate_pack
    from forge.spec import load_spec
except ModuleNotFoundError:
    print("M7a FAIL: install project dependencies from pyproject.toml first")
    raise SystemExit(1)

# A controlled external process tests the real transport; no mocking RPC or LLM decisions.
SERVER = '''import json, os, sys, time
mode = sys.argv[1]
assert 'FORGE_UNDECLARED_SECRET' not in os.environ
if mode == 'needs-env': os.environ['FORGE_UNDECLARED_SECRET']
for line in sys.stdin:
 r = json.loads(line)
 if 'id' not in r: continue
 m = r['method']
 if mode == 'timeout': time.sleep(30)
 if mode == 'malformed':
  print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':[]}), flush=True)
  continue
 if m == 'initialize': result = {'protocolVersion':'2024-11-05'}
 elif m == 'tools/list':
  names = ['echo', 'admin'] if mode == 'extra' else ([] if mode == 'missing' else ['echo'])
  result = {'tools':[{'name':n,'inputSchema':{'type':'object', 'description': 'changed' if mode == 'drift' else 'original'}} for n in names]}
 else: result = {'content':[], 'isError': False if mode == 'bad-negative' else r['params']['arguments'].get('reject', False)}
 print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':result}), flush=True)
'''


def check(work: Path) -> int:
    packs = work / 'packs'
    folder = packs / 'echo'
    (folder / 'scripts').mkdir(parents=True)
    (folder / 'scripts/server.py').write_text(SERVER)
    (folder / 'setup.py').write_text("print('{}')\n")
    descriptor = {'min_tier': 'T0', 'network': 'none', 'hosts': [], 'tools': [], 'env': [],
                  'mcp_servers': [{'name': 'echo', 'command': sys.executable,
                                   'args': ['scripts/server.py', 'normal'], 'tools': ['echo'], 'mutating': []}],
                  'scripts': ['server.py'], 'skills': [], 'fixture': {'setup': 'setup.py', 'cases': [
                      {'tool': 'echo__echo', 'args': {}, 'expect': 'ok'},
                      {'tool': 'echo__echo', 'args': {'reject': True}, 'expect': 'error'}]}}

    def save(value):
        (folder / 'pack.yaml').write_text(yaml.safe_dump(value))

    save(descriptor)
    os.environ['FORGE_UNDECLARED_SECRET'] = 'synthetic-do-not-inherit'
    evidence, code = test_pack('echo', packs, timeout=2)
    assert code == 0, evidence
    assert conformance(folder, descriptor) == evidence
    repeat, code = test_pack('echo', packs, timeout=2)
    assert code == 0 and repeat == evidence, 'conformance output is not deterministic'
    for mode in ['extra', 'missing', 'bad-negative', 'needs-env', 'timeout', 'malformed']:
        modified = copy.deepcopy(descriptor)
        modified['mcp_servers'][0]['args'][1] = mode
        save(modified)
        report, code = test_pack('echo', packs, timeout=0.3)
        assert code == 1 and report['status'] == 'failed', (mode, report)
    modified['mcp_servers'][0]['command'] = 'forge-nonexistent-runtime'
    save(modified)
    report, code = test_pack('echo', packs, timeout=2)
    assert code == 2 and report['status'] == 'skipped', report
    save(descriptor)
    evidence, code = test_pack('echo', packs, timeout=2)
    assert code == 0, evidence
    # A changed external runtime surface with unchanged descriptor/assets cannot silently reset its baseline.
    evidence['servers']['echo']['tools_digest'] = '0' * 64
    (folder / 'pack-conformance.json').write_text(json.dumps(evidence))
    for _ in range(2):
        report, code = test_pack('echo', packs, timeout=2)
        assert code == 1 and 'surface changed' in report['detail'], report
    (folder / 'pack-conformance.json').unlink()

    for change, reason in [({'min_tier': 'T0', 'mutating': ['echo']}, 'T2'),
                           ({'name': 'bad_name'}, 'server name'), ({'tools': ['bad__name']}, 'without __')]:
        modified = copy.deepcopy(descriptor)
        if 'min_tier' in change:
            modified['min_tier'] = change.pop('min_tier')
        modified['mcp_servers'][0].update(change)
        try:
            validate_pack(modified, folder)
        except ValueError as exc:
            assert reason in str(exc), exc
        else:
            raise AssertionError('invalid server contract accepted')

    sql = load_spec(ROOT / 'fixtures/sql-reader/spec.yaml')
    pack = read_pack(ROOT / 'packs/sql-readonly')
    # Contract constraints exercised without requiring any MCP runtime.
    local_sql = packs / 'sql-readonly'
    shutil.copytree(ROOT / 'packs/sql-readonly', local_sql, ignore=shutil.ignore_patterns('pack-conformance.json', '__pycache__'))
    first, report, problems = render(sql, packs)
    second, repeated, _ = render(sql, packs)
    assert not problems and first == second and report == repeated
    assert report['dependencies'][0]['status'] == 'assets_bundled'
    config = json.loads(first['.thclaws/mcp.json'])['mcpServers']['sql-readonly']
    assert config == {'command': 'python3', 'args': ['.thclaws/scripts/sql-readonly--server.py']}
    assert b'synthetic-do-not-inherit' not in b''.join(first.values())
    assert all(not path.endswith('.db') for path in first)
    for field in ['tools', 'env']:
        broken = copy.deepcopy(sql)
        if field == 'tools':
            broken['permissions']['tools'] = ['Read']
        else:
            broken['env'] = []
        assert any('required ' + field in p for p in render(broken, packs)[2])
    changed = copy.deepcopy(pack)
    changed.update(network='allowlist', hosts=['example.com'])
    (local_sql / 'pack.yaml').write_text(yaml.safe_dump(changed))
    assert any('required network' in p for p in render(sql, packs)[2])
    (local_sql / 'pack.yaml').write_text(yaml.safe_dump(pack))
    package = work / 'sql'
    generate(sql, package, packs_dir=packs)
    assert audit_package(package, packs_dir=packs, thclaws=False)['audit']['static'] == 'passed'
    unknown = copy.deepcopy(sql)
    unknown['permissions']['tools'].extend(['UnknownTool', 'unknown__rogue'])
    unknown_dir = work / 'unknown'
    generate(unknown, unknown_dir, packs_dir=packs)
    findings = audit_package(unknown_dir, packs_dir=packs, thclaws=False)
    assert any(f['rule'] == 'tools' and f['severity'] == 'warning' for f in findings['static_audit']['findings'])
    assert findings['audit']['static'] == 'passed'
    assert audit_package(unknown_dir, packs_dir=packs, thclaws=False, strict=True)['audit']['static'] == 'failed'
    print('M7a offline OK: v2 migration, deterministic config, env/tool/network bounds, negative protocol cases')

    evidence, code = test_pack('sql-readonly', ROOT / 'packs')
    assert code != 1, evidence
    if code == 2:
        print('M7a SKIP: SQL MCP runtime unavailable; conformance and native draft not completed (exit 2)')
        return 2
    assert len(evidence['cases']) == len(pack['fixture']['cases']) and all(c['passed'] for c in evidence['cases'])
    # Positive content and actual unchanged SQLite bytes, beyond isError smoke checks.
    setup = subprocess.run([sys.executable, str(ROOT / 'packs/sql-readonly/fixture/setup.py'), str(work)],
                           capture_output=True, text=True, check=True)
    fixture_env = json.loads(setup.stdout)
    database = work / 'invoices.db'
    before = database.read_bytes()
    rpc = RPC(['python3', str(ROOT / 'packs/sql-readonly/scripts/server.py')], work,
              {'PATH': os.environ.get('PATH', os.defpath), **fixture_env}, 10)
    try:
        rpc.call('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                'clientInfo': {'name': 'gate', 'version': '1'}})
        rpc.send({'method': 'notifications/initialized'})
        result = rpc.call('tools/call', {'name': 'query', 'arguments': {
            'statement': 'SELECT vendor, SUM(total) AS total FROM invoices GROUP BY vendor ORDER BY vendor'}})
        data = json.loads(result['content'][0]['text'])
        assert data['rows'] == [{'vendor': 'Example A', 'total': 300}, {'vendor': 'Example B', 'total': 50}], data
        for statement in ['DELETE FROM invoices', "ATTACH DATABASE 'extra.db' AS extra", 'PRAGMA writable_schema=ON']:
            assert rpc.call('tools/call', {'name': 'query', 'arguments': {'statement': statement}})['isError']
        assert database.read_bytes() == before and not (work / 'extra.db').exists()
    finally:
        rpc.close()
    conformed = work / 'conformed'
    generate(sql, conformed)
    native = audit_package(conformed)
    assert native['dependencies'][0]['status'] == 'conformant'
    assert exit_code(native) != 1, native['static_audit']
    if exit_code(native) == 2:
        print('M7a SKIP: SQL conformance passed; thclaws unavailable for draft promotion (exit 2)')
        return 2
    assert native['package_status'] == 'draft'
    print('M7a PASS: SQL conformance, unchanged database, native validate and sql-reader draft; no provider key')
    return 0


if __name__ == '__main__':
    try:
        with tempfile.TemporaryDirectory(prefix='forge-m7a-') as temporary:
            raise SystemExit(check(Path(temporary).resolve()))
    except (AssertionError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        print(f'M7a FAIL: {exc}')
        raise SystemExit(1)
