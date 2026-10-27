#!/usr/bin/env python3
"""M13: thClaws host + bots layout (0.138.0 `d452ece`) — harness pins the single-agent layout, builder/ is a host workspace.

Exit 0 = offline checks pass and the binary on PATH (a desktop/gui build) leaves a v2 package alone under
THCLAWS_AUTO_MIGRATE=0, migrates it without the flag (negative control), serves builder/ as a host whose agent reports
`.thclaws/bots/main` and leaves the tracked files untouched; 1 = failure; 2 = offline passed but thclaws is absent, or the
binary is a CLI-only build (no `thclaws bots` subcommand, hence no host code) so the host checks were explicitly skipped.
"""
from __future__ import annotations

import http.client
import importlib.util
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
    from forge.generate import generate, render
    from forge.live_test import COPY_IGNORE, check_layout, isolated_env, request
    from forge.spec import load_spec
except ModuleNotFoundError:
    print('M13 FAIL: install project dependencies from pyproject.toml first')
    raise SystemExit(1)

BUILDER = ROOT / 'builder'
AGENT = BUILDER / '.thclaws/bots/main'
RUNNER = '"$THCLAWS_AGENT_DIR/.thclaws/scripts/build.py"'
BARE_RUNNER = re.compile(r'\bpython3?\s+\.thclaws/')
SERVE_SITE = "'--serve', '--port'"
# Runtime state a daemon or host may create; everything else in a served tree must stay byte-identical.
RUNTIME_PREFIXES = ('.thclaws/state/', '.thclaws/sessions/', '.thclaws/bots/main/.thclaws/state/',
                    '.thclaws/bots/main/.thclaws/sessions/')
DOCS_FOR_OPERATORS = ('docs/export.md', 'docs/generator.md', 'docs/local-test-plan.md', 'builder/README.md')


def snapshot(folder: Path) -> dict[str, bytes]:
    files = {}
    for path in sorted(folder.rglob('*')):
        rel = path.relative_to(folder).as_posix()
        if any(rel.startswith(prefix) for prefix in RUNTIME_PREFIXES):
            continue
        if path.is_symlink():
            files[rel] = b'<symlink>' + os.readlink(path).encode()
        elif path.is_file():
            files[rel] = path.read_bytes()
    return files


def unchanged(before: dict[str, bytes], after: dict[str, bytes], what: str) -> None:
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert not changed, f'{what} changed files outside runtime state: {changed[:20]}'


def raises(fn, needle: str) -> None:
    try:
        fn()
    except ValueError as exc:
        assert needle in str(exc), f'expected {needle!r} in {exc}'
        return
    raise AssertionError(f'expected a ValueError mentioning {needle!r}')


def offline_harness(base: Path) -> None:
    env = isolated_env(base / 'home', base / 'settings.json')
    assert env['THCLAWS_AUTO_MIGRATE'] == '0' and env['THCLAWS_MCP_ALLOW_ALL'] == '1'
    assert env['HOME'] == str(base / 'home') and env['XDG_CONFIG_HOME'] == str(base / 'home/.config')
    assert env['THCLAWS_CONFIG'] == str(base / 'settings.json') and 'THCLAWS_API_TOKEN' not in env
    assert isolated_env(base / 'home', base / 'settings.json', 'tok')['THCLAWS_API_TOKEN'] == 'tok'
    assert isolated_env(base / 'home', base / 'settings.json', EXTRA='1')['EXTRA'] == '1'
    raises(lambda: isolated_env(base / 'home', base / 'settings.json', THCLAWS_AUTO_MIGRATE='1'), 'override')

    work = base / 'work'
    (work / '.thclaws').mkdir(parents=True)
    check_layout({'workspace_dir': str(work)}, work)
    alias = base / 'alias'
    alias.symlink_to(work, target_is_directory=True)
    check_layout({'workspace_dir': str(alias)}, work)  # macOS /tmp vs /private/tmp
    raises(lambda: check_layout({'workspace_dir': str(work / '.thclaws/bots/main')}, work), 'not the run directory')
    raises(lambda: check_layout({}, work), 'did not report workspace_dir')
    (work / '.thclaws/bots.json').write_text('{}')
    raises(lambda: check_layout({'workspace_dir': str(work)}, work), 'host workspace')

    sites = [p for p in [*ROOT.glob('forge/*.py'), *ROOT.glob('scripts/*.py')] if SERVE_SITE in p.read_text(encoding='utf-8')]
    assert sites, 'no --serve start site found'
    for path in sites:
        text = path.read_text(encoding='utf-8')
        assert 'isolated_env(' in text, f'{path.name} starts --serve without isolated_env'
        needle = "'THCLAWS_MCP_ALLOW_ALL': " + "'1'"  # assembled so this gate's own source does not match itself
        literal = text.count(needle)  # the helper's own definition is the only allowed copy
        assert literal == (1 if path.name == 'live_test.py' else 0), f'{path.name} builds a daemon env by hand'
    print(f'M13 offline PASS: isolated_env pins THCLAWS_AUTO_MIGRATE=0, check_layout rejects migrated trees, '
          f'{len(sites)} --serve sites use the helper')


def offline_builder() -> None:
    bots = json.loads((BUILDER / '.thclaws/bots.json').read_text(encoding='utf-8'))
    assert [b['slug'] for b in bots['bots']] == ['main'], bots
    host = json.loads((BUILDER / '.thclaws/settings.json').read_text(encoding='utf-8'))
    assert host.get('workspaceVersion') == 4 and 'agent' not in host, host
    for name in ('AGENTS.md', 'CLAUDE.md', 'manifest.json', '.thclaws/skills', '.thclaws/scripts'):
        assert not (BUILDER / name).exists(), f'builder/{name} must live in the agent folder, not at the host root'
    for name in ('manifest.json', 'AGENTS.md', '.thclaws/settings.json', '.thclaws/scripts/build.py',
                 '.thclaws/skills/spec-author/SKILL.md', '.thclaws/skills/pack-author/SKILL.md'):
        assert (AGENT / name).is_file(), f'agent file missing: {name}'
    assert json.loads((AGENT / '.thclaws/settings.json').read_text(encoding='utf-8'))['agent']['id'] == 'builder'
    texts = {p: p.read_text(encoding='utf-8') for p in (AGENT / 'AGENTS.md', BUILDER / 'README.md',
                                                        *sorted(AGENT.glob('.thclaws/skills/*/SKILL.md')))}
    for path, text in texts.items():
        assert not BARE_RUNNER.search(text), f'{path.relative_to(ROOT)} runs .thclaws/ by a bare relative path'
    assert RUNNER in texts[AGENT / 'AGENTS.md'] and RUNNER in texts[BUILDER / 'README.md'], 'runner must be addressed via $THCLAWS_AGENT_DIR'
    assert any(RUNNER in t for p, t in texts.items() if 'skills' in p.parts), 'the spec-author skill must show the runner command'

    spec = importlib.util.spec_from_file_location('m13_build', AGENT / '.thclaws/scripts/build.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    previous = os.environ.pop('AGENT_BUILDER_ROOT', None)
    try:
        assert module.builder_root() == ROOT.resolve(), 'build.py must find the checkout from inside .thclaws/bots/main'
    finally:
        if previous is not None:
            os.environ['AGENT_BUILDER_ROOT'] = previous
    ignored = (ROOT / '.gitignore').read_text(encoding='utf-8').splitlines()
    for entry in ('builder/.thclaws/state/', 'builder/.thclaws/bots/main/.thclaws/state/', 'builder/work/', 'builder/out/'):
        assert entry in ignored, f'.gitignore must cover {entry}'
    print('M13 offline PASS: builder/ is a host workspace with one agent at .thclaws/bots/main addressed through $THCLAWS_AGENT_DIR')


def offline_docs() -> None:
    design = (ROOT / 'docs/DESIGN.md').read_text(encoding='utf-8')
    assert '### Host + bots layout' in design and 'THCLAWS_AUTO_MIGRATE=0' in design
    for rel in DOCS_FOR_OPERATORS:
        assert 'THCLAWS_AUTO_MIGRATE' in (ROOT / rel).read_text(encoding='utf-8'), f'{rel} must tell operators about the flag'
    assert 'THCLAWS_AUTO_MIGRATE=0' in (ROOT / 'forge/atlas_export.py').read_text(encoding='utf-8'), 'registration steps must set the flag'
    files, _, problems = render(load_spec(ROOT / 'fixtures/invoice-reviewer/spec.yaml'), ROOT / 'packs')
    assert not problems, problems
    hints = json.loads(files['builder-build-report.json'])['deployment_hints']
    assert any('THCLAWS_AUTO_MIGRATE=0' in h for h in hints), hints
    print('M13 offline PASS: DESIGN, operator docs, export steps and deployment hints name THCLAWS_AUTO_MIGRATE=0')


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def stop(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()  # macOS answers EPERM for an already-exited (zombie) process group
    process.wait(timeout=10)


def wait_info(process: subprocess.Popen, port: int, token: str, seconds: float) -> dict:
    deadline = time.monotonic() + seconds
    while True:
        assert process.poll() is None, 'temporary daemon exited before readiness'
        try:
            return request(port, token, '/v1/agent/info', 2)
        except (OSError, ValueError, http.client.HTTPException):
            assert time.monotonic() < deadline, 'temporary daemon readiness timed out'
            time.sleep(0.2)


def serve(binary: str, cwd: Path, env: dict, port: int) -> subprocess.Popen:
    return subprocess.Popen([binary, '--serve', '--port', str(port)], cwd=cwd, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def gui_build(binary: str, base: Path) -> bool:
    """Only a desktop (gui feature) build carries `thclaws bots` and the host/migration code (DESIGN §4)."""
    probe = base / 'probe'
    probe.mkdir()
    result = subprocess.run([binary, 'bots', 'status', '--path', str(probe)], capture_output=True, text=True,
                            timeout=60, check=False)
    return result.returncode == 0 and 'layout' in result.stdout


def native(base: Path) -> int:
    binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
    if binary is None:
        print('M13 SKIP: thclaws not on PATH; layout behaviour of the real daemon not checked')
        return 2
    gui = gui_build(binary, base)
    validate = subprocess.run([binary, 'agent', 'validate', str(AGENT)], capture_output=True, text=True,
                              env={**os.environ, 'PYTHONPYCACHEPREFIX': str(base / 'pycache')}, timeout=120, check=False)
    assert validate.returncode == 0 and 'validates' in validate.stdout + validate.stderr, validate.stdout + validate.stderr
    home = base / 'home'
    home.mkdir()
    settings = base / 'settings.json'
    settings.write_text(json.dumps({'browserEnabled': False}))

    spec = load_spec(ROOT / 'fixtures/invoice-reviewer/spec.yaml')
    package = base / 'package'
    generate(spec, package)
    before = snapshot(package)
    token = secrets.token_urlsafe(32)
    port = free_port()
    process = serve(binary, package, isolated_env(home, settings, token), port)
    try:
        check_layout(wait_info(process, port, token, 30), package)
    finally:
        stop(process)
    unchanged(before, snapshot(package), 'serving a v2 package with THCLAWS_AUTO_MIGRATE=0')
    assert not (package / '.thclaws/bots.json').exists()
    print(f'M13 native PASS: {binary} serves a single-agent package in place under THCLAWS_AUTO_MIGRATE=0; agent dir validates')
    if not gui:
        print('M13 SKIP: thclaws is a CLI-only build (no `thclaws bots`); host conversion and host serving of builder/ not checked')
        return 2

    # Negative control: the same package without the flag is converted, which is why the harness sets it.
    control = base / 'control'
    generate(spec, control)
    env = isolated_env(home, settings, token)
    del env['THCLAWS_AUTO_MIGRATE']
    process = serve(binary, control, env, free_port())
    try:
        deadline = time.monotonic() + 30
        while not (control / '.thclaws/bots.json').exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
    finally:
        stop(process)
    assert (control / '.thclaws/bots.json').exists(), 'desktop build did not migrate the v2 package; re-check DESIGN §4 against this binary'
    assert (control / '.thclaws/bots/main/AGENTS.md').is_file() and not (control / 'AGENTS.md').exists()

    hosted = base / 'builder'
    shutil.copytree(BUILDER, hosted, ignore=lambda d, names: COPY_IGNORE(d, names) | {n for n in names if n in ('work', 'out')})
    before = snapshot(hosted)
    token = secrets.token_urlsafe(32)
    port = free_port()
    process = serve(binary, hosted, isolated_env(home, settings, token), port)
    try:
        info = wait_info(process, port, token, 90)  # the host spawns the agent process and proxies /v1 once it is ready
    finally:
        stop(process)
    reported = Path(info.get('workspace_dir', '')).resolve()
    assert reported == (hosted / '.thclaws/bots/main').resolve(), f'host must proxy to the agent folder, got {info.get("workspace_dir")}'
    skills = {s['name'] for s in info.get('skills', []) if isinstance(s, dict)}
    assert {'spec-author', 'pack-author'} <= skills, skills
    assert not info.get('mcp_servers'), 'builder agent must not load MCP servers'
    unchanged(before, snapshot(hosted), 'opening builder/ as a host')
    print('M13 native PASS: desktop build migrates an unflagged v2 copy (control), serves builder/ as a host whose agent '
          'reports .thclaws/bots/main with both skills, and leaves the tracked files unchanged')
    return 0


if __name__ == '__main__':
    try:
        with tempfile.TemporaryDirectory(prefix='forge-m13-') as temporary:
            base = Path(temporary).resolve()
            offline_harness(base / 'offline')
            offline_builder()
            offline_docs()
            raise SystemExit(native(base))
    except (AssertionError, OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f'M13 FAIL: {type(exc).__name__}: {exc}')
        raise SystemExit(1)
