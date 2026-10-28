#!/usr/bin/env python3
"""M11: builder-agent workspace — layout, forbidding instructions, build.py behaviour, native validate, live interview.

Exit 0 = offline + native validate + the live two-turn weather interview passed in every round; 1 = failure; 2 = offline
passed but thclaws and/or the provider key are absent, so native validation and/or the live interview were explicitly skipped.

The live interview is two `/agent/run` turns in one session (DESIGN §9): the request answers the seven questions without
confirming, so the model must show the permissions/env/capabilities blocks and stop; the confirmation then names the person
and the model must add `confirmed_by` and run build.py. `builder/` is a host workspace (DESIGN §4 "Host + bots layout"):
the daemon is started from the agent folder with THCLAWS_WORKSPACE_ROOT, exactly as a thClaws host starts its child.
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
    from forge.live_test import COPY_IGNORE, check_layout, isolated_env, redact, request
    from forge.spec import validate_spec
except ModuleNotFoundError:
    print("M11 FAIL: install project dependencies from pyproject.toml first")
    raise SystemExit(1)

BUILDER = ROOT / 'builder'
AGENT_REL = Path('.thclaws/bots/main')  # DESIGN §9: builder/ is a host workspace; the agent lives in its shelf folder
AGENT = BUILDER / AGENT_REL
RUNNER_REL = AGENT_REL / '.thclaws/scripts/build.py'
RUNNER = '"$THCLAWS_AGENT_DIR/.thclaws/scripts/build.py"'
BARE_RUNNER = re.compile(r'\bpython3?\s+\.thclaws/(?!bots/)')  # `.thclaws/bots/main/...` from the root is the full path, not the host's
WEATHER = ROOT / 'fixtures/builder/weather-spec.yaml'
REQUEST = ROOT / 'fixtures/builder/weather-request.md'
CONFIRM = ROOT / 'fixtures/builder/weather-confirm.md'
EVIDENCE = ROOT / 'out/m11-live'
FORBIDDEN_CLAIMS = ('deploy-ready', 'shippable', 'candidate')
REQUIRED_PHRASES = (
    'Never decide permissions, tier, tools, shell, network, write_scope, env names or secrets yourself',
    'Never invent a pack, an MCP server, a tool name or an env name',
    'Never claim that a package is draft, candidate, shippable, read-only or deploy-ready unless',
    'confirmed_by', '.thclaws/scripts/build.py', 'builder-build-report.json', 'forge pack test',
    'Never create subagents or workflows here', RUNNER, '--list-packs', 'wait for their answer',
    'run build.py in that same turn without asking again',
)


def tree(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob('*')) if p.is_file()}


def runner(workspace: Path, *argv: str, packs: Path | None = None, root: Path | None = ROOT) -> subprocess.CompletedProcess:
    """Run build.py from a workspace copy the way a shell at the workspace root would (cwd = workspace root)."""
    env = {'PATH': os.environ.get('PATH', os.defpath), 'PYTHONDONTWRITEBYTECODE': '1'}
    if root is not None:
        env['AGENT_BUILDER_ROOT'] = str(root)
    if packs is not None:
        env['AGENT_BUILDER_PACKS'] = str(packs)
    return subprocess.run([sys.executable, str(RUNNER_REL), *argv], cwd=workspace, env=env, capture_output=True,
                          text=True, check=False, timeout=600)


def build(workspace: Path, spec: Path, out: str, *extra: str, packs: Path | None = None,
          root: Path | None = ROOT) -> subprocess.CompletedProcess:
    return runner(workspace, str(spec), '--out', out, *extra, packs=packs, root=root)


def copy_builder(destination: Path) -> Path:
    """Copy the whole host workspace (host files + agent), never drafts, builds or runtime state."""
    shutil.copytree(BUILDER, destination,
                    ignore=lambda d, names: COPY_IGNORE(d, names) | {n for n in names if n in ('work', 'out')})
    return destination


def check_offline(work: Path, weather: dict) -> None:
    # 1. Layout: a host workspace whose single agent has no subagents, workflows or MCP config.
    bots = json.loads((BUILDER / '.thclaws/bots.json').read_text(encoding='utf-8'))
    assert [b['slug'] for b in bots['bots']] == ['main'], bots
    assert json.loads((BUILDER / '.thclaws/settings.json').read_text(encoding='utf-8')).get('workspaceVersion') == 4
    manifest = json.loads((AGENT / 'manifest.json').read_text())
    settings = json.loads((AGENT / '.thclaws/settings.json').read_text())
    assert manifest['permissions']['shell_execution'] == 'sandboxed' and manifest['requires']['mcp_servers'] == []
    assert settings['agent']['id'] == 'builder' and settings['agent']['name'] and settings['agent']['description']
    for skill in ('spec-author', 'pack-author'):
        text = (AGENT / '.thclaws/skills' / skill / 'SKILL.md').read_text(encoding='utf-8')
        assert text.startswith(f'---\nname: {skill}\n') and '\ndescription: ' in text.split('\n---\n')[0], skill
    assert os.access(AGENT / '.thclaws/scripts/build.py', os.X_OK)
    for forbidden in ('AGENTS.md', 'CLAUDE.md', 'manifest.json', '.thclaws/skills', '.thclaws/scripts', '.thclaws/mcp.json',
                      'work', 'out'):
        assert not (BUILDER / forbidden).exists(), f'builder/{forbidden} must not exist at the host root'
    for forbidden in ('.thclaws/agents', '.thclaws/agent_workflow', '.thclaws/mcp.json', '.thclaws/bots.json'):
        assert not (AGENT / forbidden).exists(), f'agent/{forbidden} must not be committed'
    agents = (AGENT / 'AGENTS.md').read_text(encoding='utf-8')
    for section in ('Mission', 'Interview', 'Must', 'Refuse', 'Output'):
        assert f'\n## {section}\n' in agents, section
    for phrase in REQUIRED_PHRASES:
        assert phrase in agents, phrase
    assert all(re.search(rf'^{n}\. ', agents, re.MULTILINE) for n in range(1, 8)), 'seven interview questions'
    spec_author = (AGENT / '.thclaws/skills/spec-author/SKILL.md').read_text(encoding='utf-8')
    for phrase in ('confirmed_by', RUNNER, '--list-packs', 'requires T2 and `atlas-workflow`', 'never invent its tools', 'names only'):
        assert phrase in spec_author, phrase
    for path in (AGENT / 'AGENTS.md', AGENT / '.thclaws/skills/spec-author/SKILL.md',
                 AGENT / '.thclaws/skills/pack-author/SKILL.md', BUILDER / 'README.md'):
        assert not BARE_RUNNER.search(path.read_text(encoding='utf-8')), \
            f'{path.relative_to(ROOT)} runs .thclaws/ by a bare relative path, which is the host folder under a workspace host'

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
    request_text = REQUEST.read_text(encoding='utf-8')
    assert 'do not add `confirmed_by`' in request_text and 'I confirm the permissions' not in request_text, \
        'the request fixture must not confirm in advance; confirmation is the second turn'
    confirm_text = CONFIRM.read_text(encoding='utf-8')
    assert 'confirmed_by' in confirm_text and 'seal' in confirm_text and 'build.py' in confirm_text

    # 3. build.py from a copied workspace (the live layout), driven only through its CLI from the workspace root.
    workspace = copy_builder(work / 'builder')
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

    # 5. Failures that are forge's, not the runner's: an invalid AgentSpec, a missing checkout (the copy is outside the repo,
    #    so the ancestor walk must fail loudly instead of guessing). From its committed location it needs no env at all.
    invalid = copy.deepcopy(weather)
    invalid['target'] = 'atlas-worker'
    (workspace / 'work/invalid.yaml').write_text(yaml.safe_dump(invalid))
    broken = build(workspace, Path('work/invalid.yaml'), 'out/invalid')
    assert broken.returncode == 1 and 'FAILED: AgentSpec validation' in broken.stdout and 'T2' in broken.stdout
    assert not (workspace / 'out/invalid').exists()
    orphan = build(workspace, Path('work/google-weather-forecaster/spec.yaml'), 'out/orphan', root=None)
    assert orphan.returncode == 1 and 'checkout not found' in orphan.stderr + orphan.stdout
    committed = runner(BUILDER, '--list-packs', root=None)
    assert committed.returncode == 0 and f'agent-builder checkout: {ROOT}' in committed.stdout, committed.stdout + committed.stderr

    # 6. A pack that exists: --list-packs names it, conformance evidence and generation go through the same runner.
    packs = work / 'packs'
    shutil.copytree(ROOT / 'fixtures/packs/echo', packs / 'echo', ignore=shutil.ignore_patterns('pack-conformance.json', '__pycache__'))
    listed = runner(workspace, '--list-packs', packs=packs)
    assert listed.returncode == 0 and 'pack echo: min_tier=' in listed.stdout and '__' in listed.stdout, listed.stdout + listed.stderr
    assert runner(workspace, '--list-packs', packs=packs).stdout == listed.stdout, '--list-packs is not deterministic'
    empty = work / 'no-packs'
    empty.mkdir()
    assert '(no packs' in runner(workspace, '--list-packs', packs=empty).stdout
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
    copied = 'finding warning tools: unknown tool x__y: absent; blocks shippable'
    events = [{'event': 'tool_use_start', 'data': {'id': 'b', 'name': 'Bash', 'input': {'command': 'python3 build.py s.yaml'}}},
              {'event': 'tool_use_result', 'data': {'id': 'b', 'name': 'Bash', 'output': f'package_status=unverified\n{copied}\n'}},
              {'event': 'tool_use_result', 'data': {'id': 'o', 'name': 'Bash', 'output': 'it is shippable'}}]
    assert own_words(f'{copied}\nAsk me to rebuild.', events) == 'Ask me to rebuild.'
    assert 'shippable' in own_words(f'{copied}\nIt is shippable now.', events), 'own claims must stay visible'
    assert 'shippable' in own_words('it is shippable', events), 'only build.py output counts as copied'
    print('M11 offline PASS: host workspace layout and prohibitions, weather fixtures (request without confirmation, confirm turn), '
          'build.py refusals/strip/replace/report/--list-packs, echo pack through the runner')


def native_validate() -> bool:
    binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
    if binary is None:
        print('M11 SKIP: thclaws not on PATH; builder agent not validated natively and live interview not run')
        return False
    with tempfile.TemporaryDirectory(prefix='m11-pycache-') as cache:
        result = subprocess.run([binary, 'agent', 'validate', str(AGENT)], capture_output=True, text=True,
                                env={**os.environ, 'PYTHONPYCACHEPREFIX': cache}, timeout=120, check=False)
    output = result.stdout + result.stderr  # thclaws prints the validate report on stderr
    assert result.returncode == 0 and 'validates' in output, output
    print('M11 native PASS: builder agent (.thclaws/bots/main) passes thclaws agent validate')
    return True


def serve_available(binary: str, scratch: Path) -> bool:
    """A CLI-only build refuses `--serve` outright (DESIGN §4 host + bots layout 13); the live interview then skips, explicitly."""
    probe = scratch / 'serve-probe'
    (probe / 'home').mkdir(parents=True)
    (probe / 'settings.json').write_text(json.dumps({'browserEnabled': False}))
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen([binary, '--serve', '--port', str(port)], cwd=probe,
                               env=isolated_env(probe / 'home', probe / 'settings.json', 'probe'),
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        _, stderr = process.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.wait(timeout=5)
        return True
    return 'not available' not in (stderr or '') and process.returncode == 0


def desktop_build(binary: str) -> bool:
    """Only a desktop (gui feature) build lists `bots` among its commands and carries the host code (DESIGN §4).

    `thclaws bots ...` cannot be used as the probe: a CLI-only build swallows the words as its [PROMPT] positional.
    """
    result = subprocess.run([binary, '--help'], capture_output=True, text=True, timeout=60, check=False)
    return result.returncode == 0 and re.search(r'^\s+bots\s', result.stdout, re.MULTILINE) is not None


def final_text(events: list[dict]) -> str:
    text = ''
    for event in events:
        if event['event'] in ('tool_use_start', 'skill_invoked'):
            text = ''
        elif event['event'] == 'text':
            text += event['data'].get('delta', '')
    return text


def build_calls(events: list[dict]) -> list[dict]:
    """Bash calls that run build.py for a build (listing packs through it is not a build)."""
    calls = []
    for event in events:
        if event['event'] == 'tool_use_start' and event['data'].get('name') == 'Bash':
            command = json.dumps(event['data'].get('input', {}))
            if 'build.py' in command and '--list-packs' not in command:
                calls.append(event)
    return calls


def own_words(text: str, events: list[dict]) -> str:
    """The final text minus whole lines copied from a build.py result: forge's own findings may name a status."""
    ids = {call['data'].get('id') for call in build_calls(events)}
    copied = {line.strip() for event in events
              if event['event'] == 'tool_use_result' and event['data'].get('id') in ids
              for line in str(event['data'].get('output', '')).splitlines() if line.strip()}
    return '\n'.join(line for line in text.splitlines() if line.strip() not in copied)


def live_round(work: Path, round_no: int, model: str, key_env: str, turn_timeout: float, idle_timeout: float,
               via_host: bool) -> None:
    """One isolated two-turn interview; every verdict comes from files forge wrote. Evidence is written even on failure."""
    binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
    base = work / f'live-{round_no}'
    base.mkdir()
    workspace = copy_builder(base / 'builder')
    agent_dir = workspace / AGENT_REL
    home = base / 'home'
    home.mkdir()
    settings = base / 'settings.json'
    settings.write_text(json.dumps({'browserEnabled': False}))
    token = secrets.token_urlsafe(32)
    extra = {key_env: os.environ[key_env], 'AGENT_BUILDER_ROOT': str(ROOT)}
    if not via_host:
        extra['THCLAWS_WORKSPACE_ROOT'] = str(workspace)  # what a thClaws host passes to the child it starts in the agent folder
    env = isolated_env(home, settings, token, **extra)
    hidden = [token, os.environ[key_env], str(work)]
    if model.startswith('oai/'):
        endpoint = os.environ.get('OPENAI_COMPAT_BASE_URL', '')
        assert endpoint and key_env == 'OPENAI_COMPAT_API_KEY', 'oai/ models need OPENAI_COMPAT_BASE_URL and OPENAI_COMPAT_API_KEY'
        env['OPENAI_COMPAT_BASE_URL'] = endpoint
        hidden.append(endpoint)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen([binary, '--serve', '--port', str(port)], cwd=workspace if via_host else agent_dir, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    evidence = {'round': round_no, 'model': model, 'mode': 'via-host' if via_host else 'agent-dir',
                'status': 'failed', 'detail': '', 'turns': []}
    started = time.strftime('%Y%m%dT%H%M%S')

    def turn(prompt_file: Path, session_id: str | None) -> tuple[list[dict], str | None]:
        events: list[dict] = []
        record = {'prompt': prompt_file.name, 'session_id': session_id, 'events': events, 'final_text': '', 'detail': ''}
        evidence['turns'].append(record)
        body = {'prompt': prompt_file.read_text(encoding='utf-8'), 'workspace_dir': str(agent_dir), 'model': model, 'stream': True}
        if session_id:
            body['session_id'] = session_id
        try:
            request(port, token, '/agent/run', turn_timeout, body, sink=events, idle_timeout=idle_timeout)
        except (ValueError, OSError, http.client.HTTPException) as exc:
            record['detail'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            record['final_text'] = final_text(events)
        session = next((e['data'].get('id') for e in events if e['event'] == 'session'), None)
        return events, session

    try:
        deadline = time.monotonic() + (90 if via_host else 30)
        while True:
            if process.poll() is not None:
                raise ValueError('temporary daemon exited before readiness')
            try:
                info = request(port, token, '/v1/agent/info', 2)
                break
            except (OSError, ValueError, http.client.HTTPException):
                assert time.monotonic() < deadline, 'temporary daemon readiness timed out'
                time.sleep(0.2)
        check_layout(info, agent_dir)  # the daemon must serve the agent folder, not an empty host root or a migrated tree
        assert {'spec-author', 'pack-author'} <= {s.get('name') for s in info.get('skills', []) if isinstance(s, dict)}, info.get('skills')
        assert not info.get('mcp_servers'), 'builder daemon must not load MCP servers'

        # Turn 1: seven answers, no confirmation. The builder drafts, shows the blocks and stops.
        events, session = turn(REQUEST, None)
        assert session, 'the first turn did not announce a session id'
        assert not build_calls(events), 'the builder ran build.py before the person confirmed'
        for draft in workspace.glob('work/*/spec.yaml'):
            assert 'confirmed_by' not in (yaml.safe_load(draft.read_text(encoding='utf-8')) or {}), f'{draft.name} was pre-confirmed'
        shown = evidence['turns'][0]['final_text']
        assert 'GOOGLE_WEATHER_API_KEY' in shown and 'google-weather' in shown.casefold(), 'turn 1 must show the env and capabilities blocks'
        assert 'confirm' in shown.casefold(), 'turn 1 must ask the person to confirm'

        # Turn 2: the person confirms by name. The builder records the marker and builds.
        events, _ = turn(CONFIRM, session)
        assert build_calls(events), 'the builder never ran build.py through Bash after the confirmation; a summary without it is a model claim'
        drafts = sorted(workspace.glob('work/*/spec.yaml'))
        assert len(drafts) == 1, f'expected one drafted spec, found {[d.as_posix() for d in drafts]}'
        drafted = yaml.safe_load(drafts[0].read_text(encoding='utf-8'))
        assert drafted.get('confirmed_by') == {'permissions': 'seal', 'env': 'seal', 'capabilities': 'seal'}, drafted.get('confirmed_by')
        stripped = {k: v for k, v in drafted.items() if k != 'confirmed_by'}
        validate_spec(stripped)
        assert [c['pack'] for c in stripped['capabilities']] == ['google-weather'], stripped['capabilities']
        assert stripped['permissions']['tools'] == ['google-weather__forecast'] and stripped['env'] == ['GOOGLE_WEATHER_API_KEY']
        assert stripped['permissions']['tier'] == 'T2' and stripped['target'] == 'atlas-workflow'
        reports = sorted(workspace.glob('out/*/builder-build-report.json'))
        assert len(reports) == 1, f'expected one build report, found {len(reports)}'
        recorded = json.loads(reports[0].read_text())
        assert recorded['package_status'] == 'unverified' and recorded['audit']['manifest'] == 'passed'
        assert recorded['dependencies'] == [{'pack': 'google-weather', 'status': 'missing', 'mcp_servers': []}]
        assert json.loads((reports[0].parent / 'agentspec.json').read_text()) == stripped
        summary = evidence['turns'][1]['final_text']
        assert 'missing' in summary.casefold() and 'unverified' in summary.casefold(), 'final report must repeat the forge verdict'
        claims = own_words(summary, events).casefold()  # copied forge lines such as "...; blocks shippable" are not claims
        for claim in FORBIDDEN_CLAIMS:
            assert claim not in claims, f'the builder claimed {claim!r}; only the build report may say that'
        evidence['status'] = 'passed'
    except (AssertionError, ValueError, OSError, http.client.HTTPException, yaml.YAMLError, KeyError) as exc:
        evidence['detail'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()  # macOS answers EPERM for an already-exited (zombie) process group
        process.wait(timeout=5)
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(redact(evidence, hidden), indent=2, sort_keys=True, ensure_ascii=False) + '\n'
        (EVIDENCE / f'round-{round_no}-{started}.json').write_text(payload)
        (EVIDENCE / 'report.json').write_text(payload)
    print(f'M11 live round {round_no} PASS ({evidence["mode"]}): {model} drafted, waited for confirmation, then ran build.py and '
          f'reported google-weather missing (evidence: out/m11-live/round-{round_no}-{started}.json, redacted)')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--model', default=None, help='model id for the live interview; required when a provider key is present')
    parser.add_argument('--provider-key-env', default='OPENAI_API_KEY')
    parser.add_argument('--turn-timeout', type=float, default=300, help='seconds per /agent/run turn')
    parser.add_argument('--idle-timeout', type=float, default=120, help='seconds without SSE bytes before a turn counts as stalled')
    parser.add_argument('--rounds', type=int, default=1, help='live rounds that must all pass (prompt/skill changes need 3)')
    parser.add_argument('--via-host', action='store_true',
                        help='start thclaws at the workspace root so a desktop build serves through its host (CLI-only builds skip)')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Z][A-Z0-9_]*_API_KEY', args.provider_key_env):
        print('M11 FAIL: provider-key-env must be a provider *_API_KEY env name')
        return 1
    if args.rounds < 1:
        print('M11 FAIL: --rounds must be at least 1')
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
        if not args.model:
            print('M11 FAIL: a provider key is present but --model was not given; the live model must be stated explicitly')
            return 1
        binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
        if not serve_available(binary, work):
            print('M11 SKIP: this thclaws is a CLI-only build whose --serve is disabled; the live interview needs the desktop build (exit 2)')
            return 2
        if args.via_host and not desktop_build(binary):
            print('M11 SKIP: --via-host needs a desktop (gui) build of thclaws; this binary has no `thclaws bots` (exit 2)')
            return 2
        for round_no in range(1, args.rounds + 1):
            live_round(work, round_no, args.model, args.provider_key_env, args.turn_timeout, args.idle_timeout, args.via_host)
    print(f'M11 PASS: offline, native validate and {args.rounds} live round(s)')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError,
            yaml.YAMLError, http.client.HTTPException) as exc:
        print(f'M11 FAIL: {type(exc).__name__}: {exc}')
        raise SystemExit(1)
