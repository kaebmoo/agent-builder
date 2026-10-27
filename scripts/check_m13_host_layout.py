#!/usr/bin/env python3
"""M13: thClaws host + bots layout (0.138.0 `d452ece`) — harness pins the single-agent layout, builder/ is a host workspace.

Exit 0 = offline checks pass and the binary on PATH is a desktop (gui) build that leaves a v2 package alone under
THCLAWS_AUTO_MIGRATE=0, migrates it without the flag (negative control), serves builder/ as a host whose agent reports
`.thclaws/bots/main` and leaves the tracked files untouched; 1 = failure; 2 = offline passed but thclaws is absent, or the
binary is a CLI-only build (no `bots` command, hence no host or migration code) so the host checks were explicitly skipped.
Checks that need no host (validate, serving a v2 package, serving the builder agent from its folder the way a host would)
run on every build.
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
AGENT_REL = Path('.thclaws/bots/main')
AGENT = BUILDER / AGENT_REL
RUNNER = '"$THCLAWS_AGENT_DIR/.thclaws/scripts/build.py"'
BARE_RUNNER = re.compile(r'\bpython3?\s+\.thclaws/(?!bots/)')  # `.thclaws/bots/main/...` from the root is the full path, not the host's
SERVE_SITE = "'--serve', '--port'"
# What thClaws moves into the agent folder when it upgrades a v2 workspace (bots/migrate.rs AGENT_ENTRIES at d452ece).
AGENT_ENTRIES = ('.thclaws', 'AGENTS.md', 'CLAUDE.md', 'manifest.json')
HOST_WORKSPACE_VERSION = 4
# Runtime state a daemon or host may create; everything else in a served tree must stay byte-identical.
RUNTIME_PREFIXES = ('.thclaws/state/', '.thclaws/sessions/', '.thclaws/bots/main/.thclaws/state/',
                    '.thclaws/bots/main/.thclaws/sessions/')
DOCS_FOR_OPERATORS = ('docs/export.md', 'docs/generator.md', 'docs/local-test-plan.md', 'builder/README.md')


def snapshot(folder: Path) -> dict[str, bytes]:
    files = {}
    for path in sorted(folder.rglob('*')):
        rel = path.relative_to(folder).as_posix()
        if any(rel.startswith(prefix) for prefix in RUNTIME_PREFIXES) or '__pycache__/' in rel:
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


def simulate_host_layout(workspace: Path) -> None:
    """Rearrange a single-agent folder the way thClaws 0.138.0 `d452ece` does on upgrade (DESIGN §4 "Host + bots layout" 2).

    SIMULATION ONLY: the moves and the host files are reproduced; the agent rewrites thClaws also performs
    (`agent_paths::fix`, identity minting, settings backfill) are not, and the cloud CLI-only build cannot run the real
    migration. The real conversion is the desktop-build control case in `native()`.
    """
    # The destination lives inside `.thclaws/`, one of the entries being moved, so thClaws stages first; so do we.
    staging = workspace / '.thclaws-v3-migration'
    staging.mkdir()
    for name in AGENT_ENTRIES:
        source = workspace / name
        if source.exists():
            source.rename(staging / name)
    agent = workspace / AGENT_REL
    agent.parent.mkdir(parents=True)
    staging.rename(agent)
    (workspace / '.thclaws').mkdir(exist_ok=True)
    (workspace / '.thclaws/state').mkdir(exist_ok=True)
    (workspace / '.thclaws/state/.gitignore').write_text('*\n')
    (workspace / '.thclaws/settings.json').write_text(json.dumps({'workspaceVersion': HOST_WORKSPACE_VERSION}, indent=2) + '\n')
    (workspace / '.thclaws/bots.json').write_text(
        json.dumps({'version': 1, 'bots': [{'slug': 'main', 'name': 'Main'}]}, indent=2) + '\n')


def offline_harness(base: Path) -> None:
    env = isolated_env(base / 'home', base / 'settings.json')
    assert env['THCLAWS_AUTO_MIGRATE'] == '0' and env['THCLAWS_MCP_ALLOW_ALL'] == '1'
    assert env['HOME'] == str(base / 'home') and env['XDG_CONFIG_HOME'] == str(base / 'home/.config')
    assert env['THCLAWS_CONFIG'] == str(base / 'settings.json') and 'THCLAWS_API_TOKEN' not in env
    assert isolated_env(base / 'home', base / 'settings.json', 'tok')['THCLAWS_API_TOKEN'] == 'tok'
    assert isolated_env(base / 'home', base / 'settings.json', THCLAWS_WORKSPACE_ROOT='/w')['THCLAWS_WORKSPACE_ROOT'] == '/w'
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
    assert len(sites) >= 4, f'expected the M6/M7b (live_test), M8, M11 and M13 --serve sites, found {[p.name for p in sites]}'
    needle = "'THCLAWS_MCP_ALLOW_ALL': " + "'1'"  # assembled so this gate's own source does not match itself
    for path in sites:
        text = path.read_text(encoding='utf-8')
        assert 'isolated_env(' in text, f'{path.name} starts --serve without isolated_env'
        assert text.count(needle) == (1 if path.name == 'live_test.py' else 0), f'{path.name} builds a daemon env by hand'
    print(f'M13 offline PASS: isolated_env pins THCLAWS_AUTO_MIGRATE=0, check_layout rejects migrated trees, '
          f'{len(sites)} --serve sites use the helper')


def offline_builder(base: Path) -> None:
    bots = json.loads((BUILDER / '.thclaws/bots.json').read_text(encoding='utf-8'))
    assert [b['slug'] for b in bots['bots']] == ['main'], bots
    host = json.loads((BUILDER / '.thclaws/settings.json').read_text(encoding='utf-8'))
    assert host.get('workspaceVersion') == HOST_WORKSPACE_VERSION and 'agent' not in host, host
    for name in ('AGENTS.md', 'CLAUDE.md', 'manifest.json', '.thclaws/skills', '.thclaws/scripts', '.thclaws/mcp.json'):
        assert not (BUILDER / name).exists(), f'builder/{name} must live in the agent folder, not at the host root'
    for name in ('manifest.json', 'AGENTS.md', '.thclaws/settings.json', '.thclaws/scripts/build.py',
                 '.thclaws/skills/spec-author/SKILL.md', '.thclaws/skills/pack-author/SKILL.md'):
        assert (AGENT / name).is_file(), f'agent file missing: {name}'
    assert json.loads((AGENT / '.thclaws/settings.json').read_text(encoding='utf-8'))['agent']['id'] == 'builder'

    # The committed tree is exactly what the thClaws upgrade would produce from the single-agent form (moves + host files).
    v2 = base / 'v2'
    shutil.copytree(AGENT, v2, ignore=COPY_IGNORE)
    shutil.copy(BUILDER / 'README.md', v2 / 'README.md')
    simulate_host_layout(v2)
    simulated, committed = snapshot(v2), snapshot(BUILDER)
    assert set(simulated) == set(committed), (sorted(set(simulated) ^ set(committed)))
    differing = sorted(k for k in committed if simulated[k] != committed[k])
    assert differing == ['.thclaws/bots.json', '.thclaws/settings.json'], f'agent files differ from the v2 form: {differing}'

    texts = {p: p.read_text(encoding='utf-8') for p in (AGENT / 'AGENTS.md', BUILDER / 'README.md',
                                                        *sorted(AGENT.glob('.thclaws/skills/*/SKILL.md')))}
    for path, text in texts.items():
        assert not BARE_RUNNER.search(text), f'{path.relative_to(ROOT)} runs .thclaws/ by a bare relative path'
    assert RUNNER in texts[AGENT / 'AGENTS.md'] and RUNNER in texts[BUILDER / 'README.md'], 'runner must be addressed via $THCLAWS_AGENT_DIR'
    assert '--list-packs' in texts[AGENT / 'AGENTS.md'], 'the agent must learn packs through build.py --list-packs'
    assert any(RUNNER in t for p, t in texts.items() if 'skills' in p.parts), 'the spec-author skill must show the runner command'

    sys.dont_write_bytecode = True  # never leave a __pycache__ inside the committed agent folder
    spec = importlib.util.spec_from_file_location('m13_build', AGENT / '.thclaws/scripts/build.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    previous = os.environ.pop('AGENT_BUILDER_ROOT', None)
    try:
        assert module.builder_root() == ROOT.resolve(), 'build.py must find the checkout from inside .thclaws/bots/main'
    finally:
        if previous is not None:
            os.environ['AGENT_BUILDER_ROOT'] = previous
    env = {'PATH': os.environ.get('PATH', os.defpath), 'AGENT_BUILDER_PACKS': str(ROOT / 'packs'), 'PYTHONDONTWRITEBYTECODE': '1'}
    listed = [subprocess.run([sys.executable, str(AGENT_REL / '.thclaws/scripts/build.py'), '--list-packs'], cwd=BUILDER, env=env,
                             capture_output=True, text=True, timeout=120, check=False) for _ in range(2)]
    assert listed[0].returncode == 0 and listed[0].stdout == listed[1].stdout, listed[0].stdout + listed[0].stderr
    for pack in sorted(p.name for p in (ROOT / 'packs').iterdir() if (p / 'pack.yaml').is_file()):
        assert f'pack {pack}: min_tier=' in listed[0].stdout, (pack, listed[0].stdout)
    ignored = (ROOT / '.gitignore').read_text(encoding='utf-8').splitlines()
    for entry in ('builder/.thclaws/state/', 'builder/.thclaws/bots/main/.thclaws/state/', 'builder/work/', 'builder/out/'):
        assert entry in ignored, f'.gitignore must cover {entry}'
    print('M13 offline PASS: builder/ is the simulated upgrade of its single-agent form, one agent at .thclaws/bots/main '
          'addressed through $THCLAWS_AGENT_DIR, build.py finds the checkout and lists packs deterministically')


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


def desktop_build(binary: str) -> bool:
    """Only a desktop (gui feature) build lists `bots` among its commands; a CLI-only build reads `thclaws bots ...` as a prompt."""
    result = subprocess.run([binary, '--help'], capture_output=True, text=True, timeout=60, check=False)
    return result.returncode == 0 and re.search(r'^\s+bots\s', result.stdout, re.MULTILINE) is not None


def serve_available(binary: str, base: Path) -> bool:
    """A CLI-only build refuses `--serve` outright (bin/app.rs at d452ece: 'rebuild with --features gui'), so every
    daemon-based check must be declared skipped on it rather than reported as a daemon that died."""
    probe = base / 'serve-probe'
    (probe / 'home').mkdir(parents=True)
    (probe / 'settings.json').write_text(json.dumps({'browserEnabled': False}))
    process = subprocess.Popen([binary, '--serve', '--port', str(free_port())], cwd=probe,
                               env=isolated_env(probe / 'home', probe / 'settings.json', 'probe'),
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        _, stderr = process.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        stop(process)
        return True  # still running after 3 s: serving is possible
    return 'not available' not in (stderr or '') and process.returncode == 0


def copy_builder(destination: Path) -> Path:
    shutil.copytree(BUILDER, destination,
                    ignore=lambda d, names: COPY_IGNORE(d, names) | {n for n in names if n in ('work', 'out')})
    return destination


def native(base: Path) -> int:
    binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
    if binary is None:
        print('M13 SKIP: thclaws not on PATH; layout behaviour of the real daemon not checked')
        return 2
    desktop = desktop_build(binary)
    validate = subprocess.run([binary, 'agent', 'validate', str(AGENT)], capture_output=True, text=True,
                              env={**os.environ, 'PYTHONPYCACHEPREFIX': str(base / 'pycache')}, timeout=120, check=False)
    assert validate.returncode == 0 and 'validates' in validate.stdout + validate.stderr, validate.stdout + validate.stderr
    if not serve_available(binary, base):
        print(f'M13 native PASS: {binary} validates the builder agent at .thclaws/bots/main')
        print('M13 SKIP: this thclaws is a CLI-only build whose --serve is disabled (DESIGN §4 host + bots layout 13); '
              'daemon layout checks, migration control and host serving need the desktop build (exit 2)')
        return 2
    home = base / 'home'
    home.mkdir()
    settings = base / 'settings.json'
    settings.write_text(json.dumps({'browserEnabled': False}))

    # A deployed package is served in place under the flag, on every build.
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

    # The builder agent served from its shelf folder with THCLAWS_WORKSPACE_ROOT — what a host does to its child — on every build.
    hosted = copy_builder(base / 'builder')
    agent_dir = hosted / AGENT_REL
    before = snapshot(hosted)
    token = secrets.token_urlsafe(32)
    port = free_port()
    process = serve(binary, agent_dir, isolated_env(home, settings, token, THCLAWS_WORKSPACE_ROOT=str(hosted)), port)
    try:
        info = wait_info(process, port, token, 30)
    finally:
        stop(process)
    check_layout(info, agent_dir)
    skills = {s['name'] for s in info.get('skills', []) if isinstance(s, dict)}
    assert {'spec-author', 'pack-author'} <= skills, skills
    assert not info.get('mcp_servers'), 'builder agent must not load MCP servers'
    unchanged(before, snapshot(hosted), 'serving the builder agent from its folder')
    print(f'M13 native PASS: {binary} serves a single-agent package in place under THCLAWS_AUTO_MIGRATE=0, validates the builder '
          'agent and serves it from .thclaws/bots/main with its skills and no MCP')
    if not desktop:
        print('M13 SKIP: thclaws is a CLI-only build (no `bots` command); real migration control and host serving of builder/ '
              'not checked — run this gate on the desktop build (exit 2)')
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

    # The host itself: opened at the workspace root, proxies /v1 to the agent, leaves the tracked files alone.
    hosted = copy_builder(base / 'builder-host')
    before = snapshot(hosted)
    token = secrets.token_urlsafe(32)
    port = free_port()
    process = serve(binary, hosted, isolated_env(home, settings, token), port)
    try:
        info = wait_info(process, port, token, 90)  # the host spawns the agent process and proxies /v1 once it is ready
    finally:
        stop(process)
    check_layout(info, hosted / AGENT_REL)
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
            offline_builder(base / 'offline-builder')
            offline_docs()
            raise SystemExit(native(base))
    except (AssertionError, OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f'M13 FAIL: {type(exc).__name__}: {exc}')
        raise SystemExit(1)
