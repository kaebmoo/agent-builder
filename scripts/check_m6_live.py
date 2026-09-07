#!/usr/bin/env python3
"""M6: offline adversarial SSE/daemon gate, then real SQL fixture and provider-backed live gate."""
import argparse
import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from forge.audit import audit_package, write_report
from forge.generate import generate, render
from forge.live_test import live_audit, parse_sse, validate_case
from forge.pack_test import setup_fixture, test_pack
from forge.packs import read_pack, validate_pack
from forge.spec import load_spec


def fails(call, phrase):
    try:
        call()
    except ValueError as exc:
        assert phrase in str(exc), str(exc)
    else:
        raise AssertionError('expected failure: ' + phrase)


def stream(value, prefix=''):
    return (prefix + 'event: text\ndata: ' + json.dumps({'delta': json.dumps(value)})
            + '\n\nevent: result\ndata: {}\n\ndata: [DONE]\n\n').encode()


FAKE = r'''
import http.server, json, os, sys, time
from pathlib import Path
if '--version' in sys.argv:
    print('thclaws 0.116.0'); raise SystemExit()
if 'validate' in sys.argv:
    raise SystemExit()
assert 'FORGE_TEST_SECRET' not in os.environ
assert os.environ['THCLAWS_MCP_ALLOW_ALL'] == '1'
assert json.loads(Path(os.environ['THCLAWS_CONFIG']).read_text()) == {'browserEnabled': False}
assert Path(os.environ['HOME']).is_dir()
assert Path('.thclaws/mcp.json').is_file()
spec = json.loads(Path('agentspec.json').read_text())
class Handler(http.server.BaseHTTPRequestHandler):
    count = 0
    def log_message(self, *args): pass
    def do_GET(self):
        assert self.headers['Authorization'] == 'Bearer ' + os.environ['THCLAWS_API_TOKEN']
        self.send_response(200); self.end_headers()
        self.wfile.write(json.dumps({'mcp_servers': SERVERS}).encode())
    def do_POST(self):
        if HANG:
            time.sleep(5)
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert body['stream'] and Path(body['workspace_dir']) == Path.cwd()
        assert 'session_id' not in body and body['model'] == spec['model']['id']
        case = spec['evaluation']['golden_cases'][Handler.count]
        Handler.count += 1
        assert json.loads(body['prompt']) == case['input']
        value = {'statement':'SELECT 1','columns':[], 'rows':[]} if case['expect']=='result' else {
            'refused':True,'reason':'invalid input','handoff':'owner'}
        self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.end_headers()
        events = [('tool_use_start', {'id':'t','name':'sql-readonly__query'}),
                  ('tool_use_result', {'id':'t','name':'sql-readonly__query','status':'ok','output':os.environ.get('OPENAI_COMPAT_API_KEY', os.environ.get('OPENAI_API_KEY'))})] if TOOLS else []
        events += [('text', {'delta':json.dumps(value)}), ('result', {})]
        for name, data in events:
            self.wfile.write(('event: '+name+'\ndata: '+json.dumps(data)+'\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')
http.server.HTTPServer(('127.0.0.1',int(sys.argv[-1])),Handler).serve_forever()
'''


def offline(base):
    spec = load_spec(ROOT / 'fixtures/sql-reader/spec.yaml')
    value = {'statement': 'SELECT 1', 'columns': [], 'rows': []}
    events = parse_sse(io.BytesIO(stream(value, ': keepalive\r\n\r\n')))
    assert validate_case(spec, spec['evaluation']['golden_cases'][0], events) == value
    fails(lambda: parse_sse(io.BytesIO(stream(value).replace(b'data: [DONE]\n\n', b''))), 'incomplete')
    fails(lambda: parse_sse(io.BytesIO(stream(value, 'event: error\ndata: {}\n\n'))), 'error')
    wrong = copy.deepcopy(spec)
    wrong['refusal']['schema'] = {}
    fails(lambda: validate_case(wrong, wrong['evaluation']['golden_cases'][0], events), 'ambiguous')
    fails(lambda: validate_case(spec, spec['evaluation']['golden_cases'][1], events), 'expected refusal')
    for text, reason in [('```json\n{}\n```', 'Expecting value'),
                         ('{"x":NaN}', 'non-finite JSON number'),
                         ('{"x":1,"x":2}', 'duplicate JSON key')]:
        bad = [{'event':'text','data':{'delta':text}}]
        fails(lambda bad=bad: validate_case(spec, spec['evaluation']['golden_cases'][0], bad), reason)
    packs = base / 'packs'
    folder = packs / 'sql-readonly'
    shutil.copytree(ROOT / 'packs/sql-readonly', folder, ignore=shutil.ignore_patterns('pack-conformance.json','__pycache__'))
    descriptor = read_pack(folder)
    invalid = copy.deepcopy(descriptor)
    invalid['env'].append('_INVALID')
    fails(lambda: validate_pack(invalid, folder), 'env')
    package = base / 'package'
    generate(spec, package, packs_dir=packs)
    binary = base / 'thclaws'
    def fake(tools=True, servers=True, hang=False, extra=False):
        binary.write_text('#!' + sys.executable + '\n' + f'TOOLS={tools!r}\nHANG={hang!r}\nSERVERS=' +
                          repr(([{'name':'sql-readonly','tool_count':3}] if servers else [])
                               + ([{'name':'browser','tool_count':20}] if extra else [])) + '\n' + FAKE)
        binary.chmod(0o700)
    fake()
    with patch.dict(os.environ, {'THCLAWS_BIN':str(binary), 'OPENAI_API_KEY':'synthetic-provider-key',
                                 'FORGE_TEST_SECRET':'must-not-inherit'}):
        with patch('forge.live_test.setup_fixture', return_value=({'SQL_READONLY_DSN':'synthetic-dsn',
                                                                 'SQL_READONLY_AI_ROOT':'synthetic-root'}, {})):
            report, code = live_audit(package, packs_dir=packs, timeout=3)
            assert code == 0 and report['package_status'] == 'candidate', report
            assert len(report['live_audit']['cases']) == 4
            assert 'synthetic-provider-key' not in json.dumps(report)
            write_report(package, report)
            assert not (package / '.thclaws/sessions').exists()
            compatible_spec = copy.deepcopy(spec)
            compatible_spec['model']['id'] = 'oai/test-model'
            compatible = base / 'compatible'
            generate(compatible_spec, compatible, packs_dir=packs)
            with patch.dict(os.environ, {'OPENAI_COMPAT_API_KEY':'synthetic-compatible-key',
                                         'OPENAI_COMPAT_BASE_URL':'https://example.test/v1'}):
                compatible_report, compatible_code = live_audit(
                    compatible, packs_dir=packs, timeout=3, key_env='OPENAI_COMPAT_API_KEY')
                assert compatible_code == 0, compatible_report
                assert 'synthetic-compatible-key' not in json.dumps(compatible_report)
            fake(tools=False)
            report, code = live_audit(package, packs_dir=packs, timeout=3)
            assert code == 1 and 'SSE' in report['live_audit']['detail'], report
            fake(servers=False)
            report, code = live_audit(package, packs_dir=packs, timeout=3)
            assert code == 1 and 'missing' in report['live_audit']['detail'], report
            fake(extra=True)
            report, code = live_audit(package, packs_dir=packs, timeout=3)
            assert code == 1 and "unexpected=['browser']" in report['live_audit']['detail'], report
            assert report['live_audit']['cases'] == [], 'inventory mismatch must block model calls'
            fake(hang=True)
            report, code = live_audit(package, packs_dir=packs, timeout=0.4)
            assert code == 1 and report['package_status'] == 'draft', report
        with patch('forge.live_test.setup_fixture', return_value=({}, {
                'repo':'synthetic', 'expected_ref':'expected', 'actual_ref':'other', 'matched':False})):
            report, code = live_audit(package, packs_dir=packs, timeout=1)
            assert code == 1 and 'revision' in report['live_audit']['detail'], report
            assert report['live_audit']['provenance']['sql-readonly']['actual_ref'] == 'other'
        fake()
        with patch.dict(os.environ, {'OPENAI_API_KEY':''}):
            report, code = live_audit(package, packs_dir=packs)
            assert code == 2 and report['package_status'] == 'draft', report
            write_report(package, report)
        for status in ['failed','skipped','conformant']:
            (folder / 'pack-conformance.json').write_text(json.dumps({'status':status,'pack_digest':'stale'}))
            files, _, problems = render(spec, packs)
            assert not problems and '.thclaws/mcp.json' in files
            generated = base / ('generated-' + status)
            generate(spec, generated, packs_dir=packs)
            report = audit_package(generated, packs_dir=packs)
            assert report['audit']['static'] == 'failed'
            assert any(f['rule']=='packs' for f in report['static_audit']['findings'])
        with patch.dict(os.environ, {'THCLAWS_BIN':'forge-no-binary'}):
            (folder / 'pack-conformance.json').unlink()
            report, code = live_audit(package, packs_dir=packs)
            assert code == 2 and report['package_status'] == 'unverified'
    # Provenance mismatch records the actual revision before a caller fails it.
    work = base / 'fixture'
    work.mkdir()
    actual = subprocess.run(['git','-C',str(ROOT.parent / 'AI'),'rev-parse','HEAD'], capture_output=True,text=True,check=False)
    if actual.returncode == 0:
        with patch('forge.pack_test.subprocess.run') as run:
            run.side_effect = [subprocess.CompletedProcess([],0,json.dumps({k:'x' for k in descriptor['env']}),''),
                               subprocess.CompletedProcess([],0,'0'*40+'\n','')]
            _, proof = setup_fixture(folder, descriptor, work, 1)
            assert not proof['matched'] and proof['actual_ref'] == '0'*40
    probe = subprocess.run([sys.executable, str(ROOT / 'packs/sql-readonly/fixture/setup.py'), str(work)],
                           env={'PATH':str(work)}, capture_output=True, text=True, check=False)
    assert probe.returncode == 2, 'setup must probe python3 on PATH, not the harness interpreter'
    print('M6 offline PASS: SSE, branches, isolated daemon, MCP evidence, redaction, demotion, skip and M7a fixes')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', help='explicit model override for this gate package only')
    parser.add_argument('--provider-key-env', default='OPENAI_API_KEY')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='check-m6-') as temporary:
        base = Path(temporary).resolve()
        offline(base)
        report, code = test_pack('sql-readonly', ROOT / 'packs')
        if code:
            print(f"M6 {'SKIP' if code == 2 else 'FAIL'}: SQL conformance: {report['detail']}")
            return code
        package = base / 'native'
        spec = load_spec(ROOT / 'fixtures/sql-reader/spec.yaml')
        if args.model:
            spec['model']['id'] = args.model
        generate(spec, package)
        print('M6 live model:', spec['model']['id'], flush=True)
        report, code = live_audit(package, key_env=args.provider_key_env)
        write_report(package, report)
        print(f"M6 {'PASS' if code == 0 else 'SKIP' if code == 2 else 'FAIL'}: {report['live_audit']['detail']}; package_status={report['package_status']}")
        return code


if __name__ == '__main__':
    raise SystemExit(main())
