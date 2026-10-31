#!/usr/bin/env python3
"""M12: thClaws baseline 0.138.0 (revision d452ece) — catalog, compatibility pairs, docs and native evidence.

Since M17 moved the current baseline to 0.146.0/9f69995 this gate keeps the 0.138.0 facts honest (the historical pair
stays in both tables, its catalog is unchanged) and re-collects the same native evidence on the current baseline binary.
Exit 0 = baseline files consistent and the binary on PATH is the current baseline pair with the write-boundary
diagnostic passing; 1 = inconsistency, a different binary, or a regression; 2 = consistent but no thclaws on PATH.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.generate import (
    STANDALONE_T1_THCLAWS_COMPATIBILITY,
    STANDALONE_THCLAWS_COMPATIBILITY,
    THCLAWS_BASELINE,
)

BASELINE_0138 = '0.138.0'
PREVIOUS_CATALOG = ROOT / 'patterns/tools-0.116.0.json'
ADDED_TOOLS = {'KmsEdit'}  # agent_runtime.rs registers KmsEditTool since the 0.116.0 baseline


def offline() -> None:
    revisions = STANDALONE_THCLAWS_COMPATIBILITY[BASELINE_0138]
    assert revisions == frozenset({'d452ece'}), revisions
    for legacy in ('0.116.0', '0.121.0', '0.129.0'):
        assert legacy in STANDALONE_THCLAWS_COMPATIBILITY, f'historical pair {legacy} dropped without review'
    # M14: T1 rides on the d452ece writePaths fix; the historical pairs must never certify T1 silently (M17 adds its own pair).
    assert STANDALONE_T1_THCLAWS_COMPATIBILITY.get(BASELINE_0138) == frozenset({'d452ece'}), STANDALONE_T1_THCLAWS_COMPATIBILITY
    assert set(STANDALONE_T1_THCLAWS_COMPATIBILITY) <= {BASELINE_0138, THCLAWS_BASELINE}, STANDALONE_T1_THCLAWS_COMPATIBILITY
    catalog_path = ROOT / f'patterns/tools-{BASELINE_0138}.json'
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    previous = json.loads(PREVIOUS_CATALOG.read_text(encoding='utf-8'))
    assert catalog['version'] == BASELINE_0138 and catalog['revision'] in revisions, (catalog['version'], catalog['revision'])
    tools = catalog['tools']
    assert tools == sorted(set(tools)) and set(tools) == set(previous['tools']) | ADDED_TOOLS, 'catalog differs from the reviewed diff'
    assert 'd452ece' in catalog['source'] or 'KmsEdit' in catalog['source']
    design = (ROOT / 'docs/DESIGN.md').read_text(encoding='utf-8')
    assert '### Baseline 0.138.0 / d452ece' in design and 'apply_filter' in design and 'resolve_landing' in design
    for path, phrase in (('docs/audit.md', f'tools-{BASELINE_0138}.json'), ('README.md', '0.138.0'),
                         ('docs/export.md', '0.138.0'), ('docs/generator.md', '0.138.0'), ('docs/PLAN.md', '| M12 |')):
        assert phrase in (ROOT / path).read_text(encoding='utf-8'), (path, phrase)
    # The static audit must read the baseline catalog, not a stale one.
    import tempfile

    from forge.audit import static_findings
    from forge.generate import generate
    from forge.spec import load_spec
    with tempfile.TemporaryDirectory(prefix='forge-m12-') as temporary:
        package = Path(temporary) / 'pkg'
        generate(load_spec(ROOT / 'fixtures/invoice-reviewer/spec.yaml'), package)
        findings, report = static_findings(package)
        assert report['thclaws_baseline'] == THCLAWS_BASELINE and not findings, findings
    print(f'M12 offline PASS: historical baseline {BASELINE_0138}/{min(revisions)} kept, catalog {len(tools)} tools, pairs and docs consistent')


def native() -> int:
    binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
    if binary is None:
        print('M12 SKIP: thclaws not on PATH; native baseline evidence not re-collected (exit 2)')
        return 2
    lines = [line.strip() for line in subprocess.run([binary, '--version'], capture_output=True, text=True,
                                                     check=True, timeout=30).stdout.splitlines() if line.strip()]
    revision = next((line.split(':', 1)[1].strip().split()[0] for line in lines[1:] if line.startswith('revision:')), None)
    assert lines and lines[0] == f'thclaws {THCLAWS_BASELINE}', f'{lines[:1]} is not the current baseline {THCLAWS_BASELINE}; re-check DESIGN §4 first'
    assert revision in STANDALONE_THCLAWS_COMPATIBILITY[THCLAWS_BASELINE], f'revision {revision} is not the reviewed source; re-check DESIGN §4 first'
    # builder/ is a host workspace since M13; the agent thclaws validates lives in its shelf folder (DESIGN §9).
    # thclaws validate runs `python -m py_compile` on the agent's scripts: keep the bytecode out of builder/ and off /tmp (S5443).
    with tempfile.TemporaryDirectory(prefix='m12-pycache-') as cache:
        validate = subprocess.run([binary, 'agent', 'validate', str(ROOT / 'builder/.thclaws/bots/main')], capture_output=True, text=True,
                                  env={**os.environ, 'PYTHONPYCACHEPREFIX': cache}, timeout=120, check=False)
    assert validate.returncode == 0, validate.stdout + validate.stderr
    # The T1 blocker documented for 0.116.0–0.129.0 must stay closed on this baseline; a regression fails the gate.
    boundary = subprocess.run([sys.executable, str(ROOT / 'scripts/check_m9_standalone.py'), '--write-boundary'],
                              capture_output=True, text=True, timeout=900, check=False)
    assert boundary.returncode == 0, 'write-boundary diagnostic regressed:\n' + boundary.stdout[-2000:] + boundary.stderr[-1000:]
    print(f"M12 native PASS: {lines[0]} revision {revision}; builder validates; write-boundary diagnostic passes")
    return 0


if __name__ == '__main__':
    try:
        offline()
        raise SystemExit(native())
    except (AssertionError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f'M12 FAIL: {type(exc).__name__}: {exc}')
        raise SystemExit(1)
