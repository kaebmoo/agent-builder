#!/usr/bin/env python3
"""M17: thClaws baseline 0.146.0 (revision 9f69995) — catalog, compatibility pairs, docs and native evidence.

Exit 0 = baseline files consistent and the binary on PATH is the reviewed version+revision with the write-boundary
diagnostic passing; 1 = inconsistency, a different binary, or a regression; 2 = consistent but no thclaws on PATH.
A `--version` that never returns is reported as exit 1 with the keychain hint from DESIGN §4 M17 item 9.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.generate import (
    STANDALONE_T1_THCLAWS_COMPATIBILITY,
    STANDALONE_THCLAWS_COMPATIBILITY,
    THCLAWS_BASELINE,
)
from forge.spec import NETWORK_BUILTIN_TOOLS, WRITE_BUILTIN_TOOLS

REVISION = '9f69995'
PREVIOUS_CATALOG = ROOT / 'patterns/tools-0.138.0.json'
ADDED_TOOLS = {'DocConvert'}  # tools/mod.rs::with_builtins registers DocConvertTool since the 0.138.0 baseline
KEYCHAIN_HINT = ('thclaws --version did not return: the binary is probably waiting for a macOS keychain consent dialog '
                 '(DESIGN §4 M17 item 9); rerun with THCLAWS_DISABLE_KEYCHAIN=1 in the environment')


def offline() -> None:
    assert THCLAWS_BASELINE == '0.146.0', THCLAWS_BASELINE
    revisions = STANDALONE_THCLAWS_COMPATIBILITY[THCLAWS_BASELINE]
    assert revisions == frozenset({REVISION}), revisions
    for legacy, revision in (('0.116.0', '75edc48'), ('0.121.0', '35ccf16'), ('0.129.0', '7a27129'), ('0.138.0', 'd452ece')):
        assert STANDALONE_THCLAWS_COMPATIBILITY.get(legacy) == frozenset({revision}), f'historical pair {legacy} changed without review'
    # T1 rides on native writePaths proven per binary (M14 on d452ece, M17 on 9f69995); the older T0 pairs never certify T1.
    assert STANDALONE_T1_THCLAWS_COMPATIBILITY == {'0.138.0': frozenset({'d452ece'}), THCLAWS_BASELINE: frozenset({REVISION})}, \
        STANDALONE_T1_THCLAWS_COMPATIBILITY
    # DocConvert writes a file and uploads the source to the document service (DESIGN §4 M17 item 1).
    assert 'docconvert' in WRITE_BUILTIN_TOOLS and 'docconvert' in NETWORK_BUILTIN_TOOLS
    catalog_path = ROOT / f'patterns/tools-{THCLAWS_BASELINE}.json'
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    previous = json.loads(PREVIOUS_CATALOG.read_text(encoding='utf-8'))
    assert catalog['version'] == THCLAWS_BASELINE and catalog['revision'] in revisions, (catalog['version'], catalog['revision'])
    tools = catalog['tools']
    assert tools == sorted(set(tools)) and set(tools) == set(previous['tools']) | ADDED_TOOLS, 'catalog differs from the reviewed diff'
    assert REVISION in catalog['source'] and 'DocConvert' in catalog['source']
    design = (ROOT / 'docs/DESIGN.md').read_text(encoding='utf-8')
    for phrase in (f'### Baseline {THCLAWS_BASELINE} / {REVISION}', 'DocConvert', 'THCLAWS_DISABLE_KEYCHAIN', 'officeApps', 'V3_TOMBSTONE'):
        assert phrase in design, phrase
    for path, phrase in (('docs/audit.md', f'tools-{THCLAWS_BASELINE}.json'), ('README.md', f'{THCLAWS_BASELINE}/`{REVISION}`'),
                         ('docs/export.md', THCLAWS_BASELINE), ('docs/generator.md', THCLAWS_BASELINE),
                         ('docs/local-test-plan.md', REVISION), ('docs/PLAN.md', '| M17 |'), ('CHANGELOG.md', 'M17')):
        assert phrase in (ROOT / path).read_text(encoding='utf-8'), (path, phrase)
    # The static audit must read the baseline catalog, not a stale one.
    import tempfile

    from forge.audit import static_findings
    from forge.generate import generate
    from forge.spec import load_spec
    with tempfile.TemporaryDirectory(prefix='forge-m17-') as temporary:
        package = Path(temporary) / 'pkg'
        generate(load_spec(ROOT / 'fixtures/invoice-reviewer/spec.yaml'), package)
        findings, report = static_findings(package)
        assert report['thclaws_baseline'] == THCLAWS_BASELINE and not findings, findings
    print(f'M17 offline PASS: baseline {THCLAWS_BASELINE}/{REVISION}, catalog {len(tools)} tools, pairs, tool sets and docs consistent')


def native() -> int:
    binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
    if binary is None:
        print('M17 SKIP: thclaws not on PATH; native baseline evidence not re-collected (exit 2)')
        return 2
    try:
        version = subprocess.run([binary, '--version'], capture_output=True, text=True, check=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise AssertionError(KEYCHAIN_HINT) from None
    lines = [line.strip() for line in version.stdout.splitlines() if line.strip()]
    revision = next((line.split(':', 1)[1].strip().split()[0] for line in lines[1:] if line.startswith('revision:')), None)
    assert lines and lines[0] == f'thclaws {THCLAWS_BASELINE}', f'{lines[:1]} is not the baseline {THCLAWS_BASELINE}; re-check DESIGN §4 first'
    assert revision in STANDALONE_THCLAWS_COMPATIBILITY[THCLAWS_BASELINE], f'revision {revision} is not the reviewed source; re-check DESIGN §4 first'
    # builder/ is a host workspace since M13; the agent thclaws validates lives in its shelf folder (DESIGN §9).
    validate = subprocess.run([binary, 'agent', 'validate', str(ROOT / 'builder/.thclaws/bots/main')], capture_output=True, text=True,
                              env={**os.environ, 'PYTHONPYCACHEPREFIX': '/tmp/m17-pycache'}, timeout=120, check=False)
    assert validate.returncode == 0, validate.stdout + validate.stderr
    # The writePaths boundary (traversal, symlink, dangling symlink) must hold on this baseline too (M17 item 2).
    boundary = subprocess.run([sys.executable, str(ROOT / 'scripts/check_m9_standalone.py'), '--write-boundary'],
                              capture_output=True, text=True, timeout=900, check=False)
    assert boundary.returncode == 0, 'write-boundary diagnostic regressed:\n' + boundary.stdout[-2000:] + boundary.stderr[-1000:]
    print(f"M17 native PASS: {lines[0]} revision {revision}; builder validates; write-boundary diagnostic passes")
    return 0


if __name__ == '__main__':
    try:
        offline()
        raise SystemExit(native())
    except (AssertionError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f'M17 FAIL: {type(exc).__name__}: {exc}')
        raise SystemExit(1)
