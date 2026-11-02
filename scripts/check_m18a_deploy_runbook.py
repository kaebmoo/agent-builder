#!/usr/bin/env python3
"""M18a gate: Atlas-channel deployment runbook, launcher templates and operator evidence.

Static part (always): docs/deploy-runbook.md names every step, the launchers render deterministically from the
sql-reader export with every deployment env NAME and no value, THCLAWS_AUTO_MIGRATE=0 is literal, and nothing
claims deployment_verified. Real-host part: the builder cannot run it; pass --host-evidence <dir> with the
operator's files (see the runbook, "หลักฐานสำหรับ gate") to reach exit 0. Without it the gate ends at 2 by design.
Pass --rehearsal <dir> for harness evidence from a dev machine (runbook §12); this always ends at exit 2.

Exit 0 = static + operator evidence checked; 1 = failure; 2 = static passed, real host not available.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.dont_write_bytecode = True

from render_launcher import DAEMON_ENV, FIXED_ENV, render

from forge.atlas_export import export
from forge.audit import audit_package, write_report
from forge.generate import generate
from forge.spec import load_spec

RUNBOOK = ROOT / 'docs' / 'deploy-runbook.md'
FIXTURE = ROOT / 'fixtures' / 'sql-reader' / 'spec.yaml'
RENDERER = ROOT / 'scripts' / 'render_launcher.py'
BASE_URL = 'https://worker.example.invalid:4317'
WORKSPACE_DIR = '/srv/atlas/sql-reader'
# Every step the runbook must name (task list of PLAN M18a); each string must appear verbatim.
RUNBOOK_STEPS = (
    'cargo build --features gui', 'THCLAWS_DISABLE_KEYCHAIN=1', 'thclaws --version', '9f69995',
    '.sha256', 'atlas-register.json', 'deployment.env', RENDERER.name,
    'THCLAWS_AUTO_MIGRATE=0', 'THCLAWS_API_TOKEN', 'THCLAWS_MCP_ALLOW_ALL', 'mcp_allowlist.json',
    '/v1/agent/info', 'deployment.package_root', 'deployment.mcp_servers',
    'POST /api/workers', 'POST /api/workspaces', 'node_template', '/poll', '`online`',
    '/api/workflow-runs', '`succeeded`', '## 9. Rollback', '## 10. Checklist', '--host-evidence',
    'builder ไม่เคยตั้ง `deployment_verified` เป็น true',
)
# Builder-authored prose must not claim a verified or ready deployment (AGENTS.md, DESIGN §6).
FORBIDDEN = ('deployment_verified: true', '"deployment_verified": true', 'deployment_verified=true',
             'deploy-ready', 'deployment verified', 'deploy ได้เลย')
SECRET_VALUE = re.compile(r'(?i)(token|secret|password|api_key|dsn)[ \t]*[=:][ \t]*\S')


def fail(message):
    raise AssertionError(message)


def check_runbook():
    text = RUNBOOK.read_text(encoding='utf-8')
    missing = [step for step in RUNBOOK_STEPS if step not in text]
    if missing:
        fail(f'runbook does not name: {missing}')
    claims = [claim for claim in FORBIDDEN if claim in text.lower()]
    if claims:
        fail(f'runbook claims a verified deployment: {claims}')
    for doc in ('docs/getting-started.md', 'docs/getting-started.en.md', 'docs/export.md', 'README.md'):
        if 'deploy-runbook.md' not in (ROOT / doc).read_text(encoding='utf-8'):
            fail(f'{doc} does not point at deploy-runbook.md')
    plan = (ROOT / 'docs' / 'PLAN.md').read_text(encoding='utf-8')
    if '| M18a |' not in plan or 'check_m18a_deploy_runbook.py' not in plan:
        fail('PLAN.md row M18a must name check_m18a_deploy_runbook.py')
    print(f'M18a PASS: runbook names all {len(RUNBOOK_STEPS)} steps, claims nothing, and the docs point at it')


def build_export(work):
    package = work / 'sql-reader'
    generate(load_spec(FIXTURE), package)
    write_report(package, audit_package(package))
    register, code = export(package, work / 'export', base_url=BASE_URL, workspace_dir=WORKSPACE_DIR)
    return register, code


def check_templates(work, register):
    files = render(work / 'export')
    assert files == render(work / 'export'), 'rendering is not deterministic'
    out = work / 'rendered'
    subprocess.run([sys.executable, str(RENDERER), str(work / 'export'), '--out', str(out)],
                   cwd=work, check=True, capture_output=True, text=True)
    assert {p.name: p.read_text(encoding='utf-8') for p in out.iterdir()} == files, 'CLI output differs from render()'
    refused = subprocess.run([sys.executable, str(RENDERER), str(work / 'export'),
                              '--out', str(out)], cwd=work, check=False, capture_output=True, text=True)
    assert refused.returncode == 1 and 'already exists' in refused.stderr, 'renderer overwrote an existing directory'
    deployment = register['deployment']
    names = [*DAEMON_ENV, *deployment['env']]
    assert deployment['env'], 'fixture must declare env names for this check to mean anything'
    service, plist, example = files['sql-reader.service'], files['thclaws.sql-reader.plist'], files['sql-reader.env.example']
    for text in (service, plist, example):
        for name in names:
            assert name in text, f'{name} missing from a rendered file'
            assert not re.search(rf'{name}[ \t]*=[ \t]*\S', text), f'{name} rendered with a value'
            assert f'<key>{name}</key>' not in text, f'{name} rendered as a launchd value'
        assert not SECRET_VALUE.search(text), 'secret-looking value in a rendered file'
        assert 'deployment_verified stays false' in text and 'deployment_verified: true' not in text, 'claim'
    for name in names:
        assert f'\n{name}=\n' in example, f'{name}= line missing from env example'
    for key, value in FIXED_ENV.items():
        assert f'Environment={key}={value}\n' in service and f'<key>{key}</key>\n    <string>{value}</string>' in plist, key
    assert f"WorkingDirectory={deployment['daemon_cwd']}\n" in service, 'service CWD'
    assert f"<key>WorkingDirectory</key>\n  <string>{deployment['daemon_cwd']}</string>" in plist, 'plist CWD'
    assert 'EnvironmentFile=/etc/thclaws/sql-reader.env' in service and '"/etc/thclaws/sql-reader.env"' in plist, 'env file'
    assert '--serve --bind 0.0.0.0 --port 4317' in service and '--serve --bind 0.0.0.0 --port 4317' in plist, 'serve args'
    assert 'User=thclaws' in service and '<string>thclaws</string>' in plist, 'service user'
    assert not deployment['deployment_verified'], 'export claims deployment_verified'  # status: unverified without thclaws, draft with it
    print('M18a PASS: launchd/systemd/env templates render deterministically from atlas-register.json with '
          f'{len(names)} env names, no values, THCLAWS_AUTO_MIGRATE=0 literal, daemon CWD and port from the export')


def check_evidence_json(folder):
    """Shared JSON file contract for host evidence and dev-machine rehearsals."""
    folder = Path(folder)

    def read(name):
        return json.loads((folder / name).read_text(encoding='utf-8'))

    register, info, poll, run = read('atlas-register.json'), read('agent-info.json'), read('poll.json'), read('atlas-run.json')
    deployment = register['deployment']
    assert info['workspace_dir'] == deployment['package_root'], (info.get('workspace_dir'), deployment['package_root'])
    servers = sorted(s['name'] for s in info.get('mcp_servers', info.get('agent', {}).get('mcp_servers', [])))
    assert servers == deployment['mcp_servers'], (servers, deployment['mcp_servers'])
    assert poll['worker']['status'] == 'online', poll['worker'].get('status')
    assert poll['worker']['id'] == register['worker']['id'], poll['worker'].get('id')
    assert run['run']['state'] == 'succeeded', run['run'].get('state')
    assert not deployment['deployment_verified'], 'the export in the evidence claims deployment_verified'
    return deployment, servers


def check_host_evidence(folder):
    """Operator evidence contract from the runbook ("หลักฐานสำหรับ gate"). The builder never produces these files."""
    folder = Path(folder)
    deployment, servers = check_evidence_json(folder)
    checklist = (folder / 'checklist.md').read_text(encoding='utf-8')
    assert '[x]' in checklist and '[ ]' not in checklist, 'checklist has unticked items'
    assert 'deployment_verified' in checklist, 'checklist must end with the deployment_verified item'
    print(f"M18a PASS: operator evidence at {folder}: daemon served {deployment['package_root']} with MCP "
          f"{servers or 'none'}, Atlas poll online, workflow run succeeded, checklist fully ticked by the operator")


def check_rehearsal(folder):
    folder = Path(folder)
    deployment, servers = check_evidence_json(folder)
    checklist = (folder / 'checklist.md').read_text(encoding='utf-8')
    lines = checklist.splitlines()
    marker = lines[0] if lines else ''
    daemon = re.search(r'\bdaemon=(self|launchd)(?=\s|;|$)', marker)
    atlas = re.search(r'\batlas=(hermetic|prod)(?=\s|;|$)', marker)
    assert marker.startswith('<!-- M18a rehearsal on a dev machine:') and daemon and atlas, \
        'checklist.md is not a rehearsal checklist'
    daemon, atlas = daemon[1], atlas[1]
    items = [line for line in lines if line.startswith(('- [x] ', '- [ ] '))]
    assert len(items) == 10, 'rehearsal checklist must have exactly 10 items'
    always_unticked = {0, 9}
    allowed_unticked = {0, 5, 9} | ({3} if daemon == 'self' else {8})
    for index, item in enumerate(items):
        if item.startswith('- [x] ') and index in always_unticked:
            fail(f'a rehearsal cannot tick: {item[:40]}')
        if item.startswith('- [ ] ') and index not in allowed_unticked:
            fail(f'rehearsal did not prove: {item[:60]}')
    assert FORBIDDEN[0] not in checklist, 'rehearsal checklist claims deployment_verified'
    print(f'M18a REHEARSAL on dev machine: evidence at {folder} (daemon={daemon}, atlas={atlas}): '
          f"daemon served {deployment['package_root']} with MCP {servers or 'none'}, Atlas poll online, "
          'workflow run succeeded, checklist ticked only where the harness proved it; not host evidence (exit 2)')
    return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    evidence_args = parser.add_mutually_exclusive_group()
    evidence_args.add_argument('--host-evidence', type=Path,
                              help='operator evidence directory from a real host, inside this repo, e.g. out/m18a-evidence/<date> (runbook §11)')
    evidence_args.add_argument('--rehearsal', type=Path,
                              help='harness evidence directory from scripts/rehearse_m18a.py inside this repo, '
                              'e.g. out/m18a-rehearsal/<date> (runbook §12); always exit 2')
    args = parser.parse_args()
    check_runbook()
    with tempfile.TemporaryDirectory(prefix='forge-m18a-') as temporary:
        work = Path(temporary).resolve()
        register, code = build_export(work)
        check_templates(work, register)
    if code == 2:
        print('M18a note: thclaws not on PATH, export produced no archive (templates do not need it)')
    if args.rehearsal is not None:
        evidence = os.path.realpath(args.rehearsal)
        if not evidence.startswith(str(ROOT) + os.sep):
            print(f'M18a FAIL: --rehearsal must be inside {ROOT} (runbook §12): {args.rehearsal}')
            return 1
        return check_rehearsal(Path(evidence))
    if args.host_evidence is None:
        print('M18a SKIP: static half passed; real host not available (exit 2). Pass --host-evidence <dir> with the '
              'operator files from docs/deploy-runbook.md §11 after a real Atlas deployment; the builder never '
              'sets deployment_verified')
        return 2
    evidence = os.path.realpath(args.host_evidence)
    if not evidence.startswith(str(ROOT) + os.sep):  # runbook §11: evidence lives in the gitignored out/ tree of this repo
        print(f'M18a FAIL: --host-evidence must be inside {ROOT} (runbook §11): {args.host_evidence}')
        return 1
    check_host_evidence(Path(evidence))
    return 0


if __name__ == '__main__':
    try:
        status = main()
    except (AssertionError, ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        print(f'M18a FAIL: {type(exc).__name__}: {exc}')
        status = 1
    raise SystemExit(status)
