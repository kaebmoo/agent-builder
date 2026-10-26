#!/usr/bin/env python3
"""M11: builder-agent workspace — layout, forbidding instructions, build.py behaviour, native validate, live interview.

Exit 0 = offline + native validate + live weather interview passed; 1 = failure; 2 = offline passed but thclaws
and/or the provider key are absent, so native validation and/or the live interview were explicitly skipped.
"""
from __future__ import annotations

import argparse
import copy
import http.client
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import yaml

    from forge.generate import render
    from forge.live_test import COPY_IGNORE, redact, request
    from forge.spec import validate_spec
except ModuleNotFoundError:
    print("M11 FAIL: install project dependencies from pyproject.toml first")
    raise SystemExit(1)

BUILDER = ROOT / 'builder'
WEATHER = ROOT / 'fixtures/builder/weather-spec.yaml'
REQUEST = ROOT / 'fixtures/builder/weather-request.md'
FORBIDDEN_CLAIMS = ('deploy-ready', 'shippable', 'candidate')
REQUIRED_PHRASES = (
    'Never decide permissions, tier, tools, shell, network, write_scope, env names or secrets yourself',
    'Never invent a pack, an MCP server, a tool name or an env name',
    'Never claim that a package is draft, candidate, shippable, read-only or deploy-ready unless',
    'confirmed_by', '.thclaws/scripts/build.py', 'builder-build-report.json', 'forge pack test',
    'Never create subagents or workflows here',
)


def tree(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob('*')) if p.is_file()}


def build(workspace: Path, spec: Path, out: str, *extra: str, packs: Path | None = None,
          root: Path | None = ROOT) -> subprocess.CompletedProcess:
    env = {'PATH': os.environ.get('PATH', os.defpath)}
    if root is not None:
        env['AGENT_BUILDER_ROOT'] = str(root)
    if packs is not None:
        env['AGENT_BUILDER_PACKS'] = str(packs)
    return subprocess.run([sys.executable, '.thclaws/scripts/build.py', str(spec), '--out', out, *extra],
                          cwd=workspace, env=env, capture_output=True, text=True, check=False, timeout=600)


def check_offline(work: Path, weather: dict) -> None:
    # 1. Layout: an agent package without subagents, workflows or MCP config.
    manifest = json.loads((BUILDER / 'manifest.json').read_text())
    settings = json.loads((BUILDER / '.thclaws/settings.json').read_text())
    assert manifest['permissions']['shell_execution'] == 'sandboxed' and manifest['requires']['mcp_servers'] == []
    assert settings['agent']['id'] == 'builder' and settings['agent']['name'] and settings['agent']['description']
    for skill in ('spec-author', 'pack-author'):
        text = (BUILDER / '.thclaws/skills' / skill / 'SKILL.md').read_text(encoding='utf-8')
        assert text.startswith(f'---\nname: {skill}\n') and '\ndescription: ' in text.split('\n---\n')[0], skill
    assert os.access(BUILDER / '.thclaws/scripts/build.py', os.X_OK)
    for forbidden in ('.thclaws/agents', '.thclaws/agent_workflow', '.thclaws/mcp.json', 'work', 'out'):
        assert not (BUILDER / forbidden).exists(), f'builder/{forbidden} must not be committed'
    agents = (BUILDER / 'AGENTS.md').read_text(encoding='utf-8')
    for section in ('Mission', 'Interview', 'Must', 'Refuse', 'Output'):
        assert f'\n## {section}\n' in agents, section
    for phrase in REQUIRED_PHRASES:
        assert phrase in agents, phrase
    assert all(re.search(rf'^{n}\. ', agents, re.MULTILINE) for n in range(1, 8)), 'seven interview questions'
    spec_author = (BUILDER / '.thclaws/skills/spec-author/SKILL.md').read_text(encoding='utf-8')
    for phrase in ('confirmed_by', 'build.py', 'requires T2 and `atlas-workflow`', 'never invent its tools', 'names only'):
        assert phrase in spec_author, phrase

    # 2. The weather fixture is what the builder should produce: forge accepts it and reports the pack missing.
    stripped = {k: v for k, v in weather.items() if k != 'confirmed_by'}
    validate_spec(stripped)
    files, report, problems = render(stripped)
    assert not problems and report['dependencies'] == [{'pack': 'google-weather', 'status': 'missing', 'mcp_servers': []}]
    assert '.thclaws/mcp.json' not in files and b'GOOGLE_WEATHER_API_KEY' in files['agentspec.json']
    try:
        validate_spec(weather)
    except ValueError:
        pass
    else:
        raise AssertionError('forge accepted confirmed_by; the marker must stay builder-only')

    # 3. build.py from a copied workspace (the live layout), driven only through its CLI.
    workspace = work / 'builder'
    shutil.copytree(BUILDER, workspace, ignore=COPY_IGNORE)
    (workspace / 'work/google-weather-forecaster').mkdir(parents=True)
    spec_path = workspace / 'work/google-weather-forecaster/spec.yaml'
    shutil.copy(WEATHER, spec_path)
    first = build(workspace, Path('work/google-weather-forecaster/spec.yaml'), 'out/google-weather-forecaster')
    assert first.returncode == 1, first.stdout + first.stderr
    for line in ('package_status=unverified', 'audit: spec=passed', 'static=failed',
                 'pack google-weather: missing (no descriptor under', 'RESULT: FAIL (exit 1)'):
        assert line in first.stdout, (line, first.stdout)
    package = workspace / 'out/google-weather-forecaster'
    generated = json.loads((package / 'agentspec.json').read_text())
    assert 'confirmed_by' not in generated and generated == stripped
    recorded = json.loads((package / 'builder-build-report.json').read_text())
    assert recorded['dependencies'][0]['status'] == 'missing' and recorded['audit']['static'] == 'failed'
    before = tree(package)
    again = build(workspace, Path('work/google-weather-forecaster/spec.yaml'), 'out/google-weather-forecaster')
    assert again.returncode == 1 and 'already exists' in again.stdout and tree(package) == before
    replaced = build(workspace, Path('work/google-weather-forecaster/spec.yaml'), 'out/google-weather-forecaster', '--replace')
    assert replaced.returncode == 1 and tree(package) == before, 'rebuild is not byte-identical'
    foreign = workspace / 'out/foreign'
    foreign.mkdir()
    (foreign / 'notes.txt').write_text('operator file')
    kept = build(workspace, Path('work/google-weather-forecaster/spec.yaml'), 'out/foreign', '--replace')
    assert kept.returncode == 1 and 'already exists' in kept.stdout and (foreign / 'notes.txt').is_file()

    # 4. Refusals: no marker, wrong keys, reserved or empty confirmers. Nothing is generated.
    def refused(spec: dict, reason: str, label: str) -> None:
        path = workspace / f'work/{label}.yaml'
        path.write_text(yaml.safe_dump(spec))
        result = build(workspace, Path(f'work/{label}.yaml'), f'out/{label}')
        assert result.returncode == 1 and result.stdout.startswith('REFUSED: ') and reason in result.stdout, (label, result.stdout)
        assert not (workspace / f'out/{label}').exists(), label

    refused(stripped, 'no confirmed_by block', 'unconfirmed')
    partial = copy.deepcopy(weather)
    del partial['confirmed_by']['env']
    refused(partial, "exactly ['permissions', 'env', 'capabilities']", 'partial')
    extra = copy.deepcopy(weather)
    extra['confirmed_by']['network'] = 'seal'
    refused(extra, "exactly ['permissions', 'env', 'capabilities']", 'extra-key')
    for who in ('model', 'Builder', 'assistant', '', '  ', None):
        reserved = copy.deepcopy(weather)
        reserved['confirmed_by']['permissions'] = who
        refused(reserved, 'name of the person who confirmed', f'reserved-{abs(hash(who))}')
    scalar = copy.deepcopy(weather)
    scalar['confirmed_by'] = 'seal'
    refused(scalar, 'no confirmed_by block', 'scalar')

    # 5. Failures that are forge's, not the runner's: an invalid AgentSpec, a missing checkout.
    invalid = copy.deepcopy(weather)
    invalid['target'] = 'atlas-worker'
    (workspace / 'work/invalid.yaml').write_text(yaml.safe_dump(invalid))
    broken = build(workspace, Path('work/invalid.yaml'), 'out/invalid')
    assert broken.returncode == 1 and 'FAILED: AgentSpec validation' in broken.stdout and 'T2' in broken.stdout
    assert not (workspace / 'out/invalid').exists()
    orphan = build(workspace, Path('work/google-weather-forecaster/spec.yaml'), 'out/orphan', root=None)
    assert orphan.returncode == 1 and 'checkout not found' in orphan.stderr + orphan.stdout

    # 6. A pack that exists: conformance evidence and generation through the same runner (echo reference pack).
    packs = work / 'packs'
    shutil.copytree(ROOT / 'fixtures/packs/echo', packs / 'echo', ignore=shutil.ignore_patterns('pack-conformance.json', '__pycache__'))
    echo = yaml.safe_load((ROOT / 'fixtures/packs/echo/spec.yaml').read_text())
    echo['confirmed_by'] = {'permissions': 'seal', 'env': 'seal', 'capabilities': 'seal'}
    (workspace / 'work/echo.yaml').write_text(yaml.safe_dump(echo))
    built = build(workspace, Path('work/echo.yaml'), 'out/echo-reader', packs=packs)
    assert built.returncode in (0, 2), built.stdout + built.stderr
    assert 'pack echo: conformant (forge pack test: PASS)' in built.stdout and 'static=passed' in built.stdout
    assert (packs / 'echo/pack-conformance.json').is_file()
    binary_present = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws')) is not None
    assert built.returncode == (0 if binary_present else 2), built.stdout
    assert ('package_status=draft' if binary_present else 'package_status=unverified') in built.stdout
    print('M11 offline PASS: workspace layout and prohibitions, weather fixture accepted by forge with google-weather missing, '
          'build.py refusals/strip/replace/report, echo pack through the runner')


def native_validate() -> bool:
    binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
    if binary is None:
        print('M11 SKIP: thclaws not on PATH; builder workspace not validated natively and live interview not run')
        return False
    with tempfile.TemporaryDirectory(prefix='m11-pycache-') as cache:
        result = subprocess.run([binary, 'agent', 'validate', str(BUILDER)], capture_output=True, text=True,
                                env={**os.environ, 'PYTHONPYCACHEPREFIX': cache}, timeout=120, check=False)
    output = result.stdout + result.stderr  # thclaws prints the validate report on stderr
    assert result.returncode == 0 and 'validates' in output, output
    print('M11 native PASS: builder workspace passes thclaws agent validate')
    return True


def live_interview(work: Path, model: str, key_env: str, timeout: float) -> None:
    """One isolated daemon run of the weather request; every verdict comes from files forge wrote."""
    binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
    workspace = work / 'live-builder'
    shutil.copytree(BUILDER, workspace, ignore=COPY_IGNORE)
    home = work / 'live-home'
    home.mkdir()
    settings = work / 'live-settings.json'
    settings.write_text(json.dumps({'browserEnabled': False}))
    token = secrets.token_urlsafe(32)
    env = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(home), 'XDG_CONFIG_HOME': str(home / '.config'),
           'THCLAWS_CONFIG': str(settings), 'THCLAWS_MCP_ALLOW_ALL': '1', 'THCLAWS_API_TOKEN': token,
           'AGENT_BUILDER_ROOT': str(ROOT), key_env: os.environ[key_env]}
    hidden = [token, os.environ[key_env], str(work)]
    if model.startswith('oai/'):
        endpoint = os.environ.get('OPENAI_COMPAT_BASE_URL', '')
        assert endpoint and key_env == 'OPENAI_COMPAT_API_KEY', 'oai/ models need OPENAI_COMPAT_BASE_URL and OPENAI_COMPAT_API_KEY'
        env['OPENAI_COMPAT_BASE_URL'] = endpoint
        hidden.append(endpoint)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen([binary, '--serve', '--port', str(port)], cwd=workspace, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    evidence = {'model': model, 'status': 'failed', 'events': [], 'final_text': '', 'detail': ''}
    try:
        deadline = time.monotonic() + 30
        while True:
            if process.poll() is not None:
                raise ValueError('temporary daemon exited before readiness')
            try:
                info = request(port, token, '/v1/agent/info', 1)
                break
            except (OSError, ValueError, http.client.HTTPException):
                assert time.monotonic() < deadline, 'temporary daemon readiness timed out'
                time.sleep(0.1)
        assert not info.get('mcp_servers'), 'builder daemon must not load MCP servers'
        events = request(port, token, '/agent/run', timeout,
                         {'prompt': REQUEST.read_text(encoding='utf-8'), 'workspace_dir': str(workspace),
                          'model': model, 'stream': True})
        evidence['events'] = redact(events, hidden)
        text = ''
        for event in events:
            if event['event'] in ('tool_use_start', 'skill_invoked'):
                text = ''
            elif event['event'] == 'text':
                text += event['data'].get('delta', '')
        evidence['final_text'] = redact(text, hidden)
        builds = [e for e in events if e['event'] == 'tool_use_start' and e['data'].get('name') == 'Bash'
                  and 'build.py' in json.dumps(e['data'].get('input', {}))]
        assert builds, 'the builder never ran build.py through Bash; a summary without it is a model claim'
        drafts = sorted((workspace / 'work').glob('*/spec.yaml'))
        assert len(drafts) == 1, f'expected one drafted spec, found {[d.as_posix() for d in drafts]}'
        drafted = yaml.safe_load(drafts[0].read_text(encoding='utf-8'))
        assert drafted.get('confirmed_by') == {'permissions': 'seal', 'env': 'seal', 'capabilities': 'seal'}, drafted.get('confirmed_by')
        stripped = {k: v for k, v in drafted.items() if k != 'confirmed_by'}
        validate_spec(stripped)
        assert [c['pack'] for c in stripped['capabilities']] == ['google-weather'], stripped['capabilities']
        assert stripped['permissions']['tools'] == ['google-weather__forecast'] and stripped['env'] == ['GOOGLE_WEATHER_API_KEY']
        assert stripped['permissions']['tier'] == 'T2' and stripped['target'] == 'atlas-workflow'
        reports = sorted((workspace / 'out').glob('*/builder-build-report.json'))
        assert len(reports) == 1, f'expected one build report, found {len(reports)}'
        recorded = json.loads(reports[0].read_text())
        assert recorded['package_status'] == 'unverified' and recorded['audit']['manifest'] == 'passed'
        assert recorded['dependencies'] == [{'pack': 'google-weather', 'status': 'missing', 'mcp_servers': []}]
        assert json.loads((reports[0].parent / 'agentspec.json').read_text()) == stripped
        lowered = text.casefold()
        assert 'missing' in lowered and 'unverified' in lowered, 'final report must repeat the forge verdict'
        for claim in FORBIDDEN_CLAIMS:
            assert claim not in lowered, f'final report claims {claim!r} without forge evidence'
        evidence['status'] = 'passed'
    except (AssertionError, ValueError, OSError, http.client.HTTPException) as exc:
        evidence['detail'] = redact(str(exc), hidden)
        raise
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.wait(timeout=5)
        out = ROOT / 'out/m11-live'
        out.mkdir(parents=True, exist_ok=True)
        (out / 'report.json').write_text(json.dumps(evidence, indent=2, sort_keys=True, ensure_ascii=False) + '\n')
    print(f'M11 live PASS: {model} drafted a forge-accepted spec, ran build.py and reported google-weather missing '
          f'(evidence: out/m11-live/report.json, redacted)')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--model', default='gpt-5.4', help='model id for the live interview (not written to any fixture)')
    parser.add_argument('--provider-key-env', default='OPENAI_API_KEY')
    parser.add_argument('--timeout', type=float, default=900, help='seconds for the whole live interview')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Z][A-Z0-9_]*_API_KEY', args.provider_key_env):
        print('M11 FAIL: provider-key-env must be a provider *_API_KEY env name')
        return 1
    weather = yaml.safe_load(WEATHER.read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='forge-m11-') as temporary:
        work = Path(temporary).resolve()
        check_offline(work, weather)
        if not native_validate():
            return 2
        if not os.environ.get(args.provider_key_env, '').strip():
            print(f'M11 SKIP: no provider key in {args.provider_key_env}; live weather interview not run (exit 2)')
            return 2
        live_interview(work, args.model, args.provider_key_env, args.timeout)
    print('M11 PASS: offline, native validate and live interview')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError,
            yaml.YAMLError, http.client.HTTPException) as exc:
        print(f'M11 FAIL: {type(exc).__name__}: {exc}')
        raise SystemExit(1)
