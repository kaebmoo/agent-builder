#!/usr/bin/env python3
"""M18b gate: the self-use bot channel: `thclaws agent pack` archive -> catalogue -> `thclaws bots add` / host `POST /bots` -> installed tree.

Exit 0 = offline and native checks passed; 1 = failure (a non-baseline binary, or a DESIGN §4 "Bot channel" claim that no
longer holds); 2 = offline passed but thclaws is absent or a CLI-only build (no `bots` command, DESIGN §4 host + bots layout 1/13).
The catalogue is a loopback stub that serves the archive this gate packs (the bytes `/cloud publish` uploads). No catalogue
account, provider key or model call: the `-p` run of the installed bot is evidence under out/m18b-evidence/ (PLAN M18b), not a gate.
"""
from __future__ import annotations

import hashlib
import http.client
import http.server
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

try:
    from forge.generate import STANDALONE_THCLAWS_COMPATIBILITY, THCLAWS_BASELINE, generate
    from forge.live_test import isolated_env
    from forge.spec import load_spec
except ModuleNotFoundError:
    print('M18b FAIL: install project dependencies from pyproject.toml first')
    raise SystemExit(1)

SLUG = 'text-reviewer-standalone'
IDENTITY_FILES = ['.thclaws/settings.json', 'manifest.json']  # split_unified_manifest rewrites exactly these (DESIGN §4 M18b 4)
IDENTITY_KEYS = {'id', 'name', 'description', 'author'}
DEFAULT_MODEL = 'claude-sonnet-4-6'  # AppConfig default that seed_workspace_model falls back to (DESIGN §4 M18b 5)
HOST_MODEL = 'host-model-probe'
DOCS = {
    'docs/DESIGN.md': ('### Bot channel', '9f69995', 'seed_workspace_model', 'split_unified_manifest', 'no agents listed',
                       '/cloud publish', 'X-Agent-SHA256', 'X-Agent-UUID', 'POST /bots'),
    'docs/PLAN.md': ('| M18b |', 'M18b ทำ (Mac)', 'check_m18b_bot_channel.py'),
    'README.md': ('M18b',),
    'docs/getting-started.md': ('thclaws bots add', '"Bot channel"'),
    'docs/getting-started.en.md': ('thclaws bots add', '"Bot channel"'),
}


class Catalogue(http.server.BaseHTTPRequestHandler):
    """Loopback stand-in for the thClaws catalogue client contract (DESIGN §4 M18b 2)."""

    archive = b''
    sha256 = ''
    agent_uuid = ''
    token = ''
    requests: ClassVar[list[dict]] = []

    def log_message(self, *_):
        pass

    def do_GET(self):
        auth = self.headers.get('Authorization', '')
        Catalogue.requests.append({'path': self.path, 'token_ok': auth == f'Bearer {Catalogue.token}'})
        if self.path.split('?')[0] == f'/api/agents/{SLUG}/download':
            if not auth.startswith('Bearer '):
                return self.reply(401, {'detail': 'missing bearer'})
            self.send_response(200)
            self.send_header('Content-Type', 'application/gzip')
            self.send_header('Content-Length', str(len(Catalogue.archive)))
            self.send_header('X-Agent-Version', '0.1.0')
            self.send_header('X-Agent-SHA256', Catalogue.sha256)
            self.send_header('X-Agent-UUID', Catalogue.agent_uuid)
            self.end_headers()
            self.wfile.write(Catalogue.archive)
            return
        if self.path.startswith('/api/agents'):
            return self.reply(200, [{'slug': SLUG, 'name': SLUG, 'version': '0.1.0', 'uuid': Catalogue.agent_uuid}])
        if self.path.startswith('/api/auth/me'):
            return self.reply(200, {'can_publish': False, 'email': 'stub@localhost'})
        self.reply(404, {'detail': 'not found'})

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def call(port, method, path, body=None, token=None, timeout=30):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    connection.request(method, path, body=data, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    try:
        return response.status, json.loads(raw)
    except ValueError:
        return response.status, raw.decode(errors='replace')[:500]


def desktop_build(binary):
    """Only a desktop (gui feature) build has `bots`; a CLI-only build has neither `bots` nor `--serve` (DESIGN §4 M13 1/13)."""
    result = subprocess.run([binary, '--help'], capture_output=True, text=True, timeout=60, check=False,
                            env={**os.environ, 'THCLAWS_DISABLE_KEYCHAIN': '1'})
    return result.returncode == 0 and re.search(r'^\s+bots\s', result.stdout, re.MULTILINE) is not None


def scaffold_host(workspace, cloud_url, slugs=('main',), model=None):
    """Host root (workspaceVersion 4) plus blank bots; thClaws refuses a bots.json without agents (DESIGN §4 M18b 3)."""
    settings = {'workspaceVersion': 4, 'cloud': {'url': cloud_url}}
    if model:
        settings['model'] = model
    (workspace / '.thclaws').mkdir(parents=True)
    (workspace / '.thclaws/settings.json').write_text(json.dumps(settings, indent=2))
    (workspace / '.thclaws/bots.json').write_text(json.dumps({'version': 1, 'bots': [{'slug': s} for s in slugs]}, indent=2))
    for slug in slugs:
        (workspace / '.thclaws/bots' / slug / '.thclaws').mkdir(parents=True)
        (workspace / '.thclaws/bots' / slug / '.thclaws/settings.json').write_text(json.dumps({'workspaceVersion': 2}))


def slugs(workspace):
    return [b['slug'] for b in json.loads((workspace / '.thclaws/bots.json').read_text())['bots']]


def compare(archive, bot_dir):
    """Installed files versus archive members: the install may rewrite the identity files and add runtime state only."""
    with tarfile.open(archive) as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    differs = sorted(n for n, data in members.items() if (bot_dir / n).exists() and (bot_dir / n).read_bytes() != data)
    missing = sorted(n for n in members if not (bot_dir / n).exists())
    extra = sorted(p.relative_to(bot_dir).as_posix() for p in bot_dir.rglob('*') if p.is_file()
                   and p.relative_to(bot_dir).as_posix() not in members)
    assert not missing, f'{bot_dir}: archive members missing after install: {missing}'
    assert differs == IDENTITY_FILES, f'{bot_dir}: install rewrote {differs}, expected exactly {IDENTITY_FILES} (DESIGN §4 M18b 4)'
    return extra, len(members)


def installed_identity(bot_dir, source_settings, agent_uuid):
    settings = json.loads((bot_dir / '.thclaws/settings.json').read_text())
    manifest = json.loads((bot_dir / 'manifest.json').read_text())
    assert settings['agent'] == {**source_settings['agent'], 'uuid': agent_uuid}, settings['agent']
    assert settings['workspaceVersion'] == 2 and settings['officeApps'] == [], settings
    assert not (IDENTITY_KEYS & set(manifest)), f'installed manifest.json still carries identity: {sorted(IDENTITY_KEYS & set(manifest))}'
    return settings['model']


def stop(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()
    process.wait(timeout=10)


def offline(base):
    for path, phrases in DOCS.items():
        text = (ROOT / path).read_text(encoding='utf-8')
        for phrase in phrases:
            assert phrase in text, f'{path} lacks {phrase!r}'
    package = base / 'package'
    generate(load_spec(ROOT / f'fixtures/{SLUG}/spec.yaml'), package)
    settings = json.loads((package / '.thclaws/settings.json').read_text(encoding='utf-8'))
    assert settings['model'] and settings['agent']['id'] == SLUG, settings
    print(f'M18b offline PASS: DESIGN §4 "Bot channel", PLAN, README and getting-started describe the channel; '
          f'{SLUG} generates with model {settings["model"]} pinned')
    return package, settings


def native(base, package, source_settings):
    binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
    if binary is None:
        print('M18b SKIP: thclaws not on PATH; catalogue install (bots add, POST /bots) not checked')
        return 2
    version = subprocess.run([binary, '--version'], capture_output=True, text=True, timeout=30, check=True,
                             env={**os.environ, 'THCLAWS_DISABLE_KEYCHAIN': '1'})
    lines = [line.strip() for line in version.stdout.splitlines() if line.strip()]
    assert lines and lines[0] == f'thclaws {THCLAWS_BASELINE}', f'{lines[:1]} is not the baseline {THCLAWS_BASELINE}; re-check DESIGN §4 first'
    found = re.search(r'revision:\s*(\S+)', version.stdout)
    revision = found.group(1) if found else ''
    assert revision in STANDALONE_THCLAWS_COMPATIBILITY[THCLAWS_BASELINE], f'revision {revision} is not the reviewed source; re-check DESIGN §4 first'
    if not desktop_build(binary):
        print('M18b SKIP: thclaws is a CLI-only build (no `bots` command, DESIGN §4 host + bots layout 1/13); run this gate on the desktop build')
        return 2

    home = base / 'home'
    (home / '.config').mkdir(parents=True)
    settings = base / 'settings.json'
    settings.write_text(json.dumps({'browserEnabled': False}))
    archive = base / f'{SLUG}-0.1.0.tar.gz'
    pack = subprocess.run([binary, 'agent', 'pack', str(package), '--out', str(archive)], capture_output=True, text=True,
                          timeout=120, check=False, env=isolated_env(home, settings, None, THCLAWS_DISABLE_KEYCHAIN='1'))
    assert pack.returncode == 0 and archive.exists(), pack.stdout + pack.stderr

    Catalogue.archive = archive.read_bytes()
    Catalogue.sha256 = hashlib.sha256(Catalogue.archive).hexdigest()
    Catalogue.agent_uuid = str(uuid.uuid4())
    Catalogue.token = secrets.token_urlsafe(24)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Catalogue)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cloud_url = f'http://127.0.0.1:{server.server_address[1]}'
    api_token = secrets.token_urlsafe(32)
    env = isolated_env(home, settings, api_token, THCLAWS_CLOUD_URL=cloud_url, THCLAWS_CLOUD_TOKEN=Catalogue.token,
                       THCLAWS_DISABLE_KEYCHAIN='1')

    def bots_add(workspace):
        seen = len(Catalogue.requests)
        result = subprocess.run([binary, 'bots', 'add', SLUG, '--path', str(workspace)], capture_output=True, text=True,
                                timeout=120, check=False, env=env, cwd=workspace)
        return result, Catalogue.requests[seen:]

    try:
        # 3: a host without agents is refused, after the archive was already extracted.
        empty = base / 'host-empty'
        scaffold_host(empty, cloud_url, slugs=())
        result, fetched = bots_add(empty)
        output = result.stdout + result.stderr
        assert result.returncode != 0 and 'no agents listed' in output, output
        assert (empty / '.thclaws/bots' / SLUG / 'manifest.json').exists() and slugs(empty) == [], \
            'bots add no longer extracts before registering; re-check DESIGN §4 M18b 3'

        # 1, 2, 4, 5: CLI install from the catalogue with the correct bearer; identity split; pinned model replaced by the default.
        cli = base / 'host-cli'
        scaffold_host(cli, cloud_url)
        result, fetched = bots_add(cli)
        assert result.returncode == 0, result.stdout + result.stderr
        assert [r['token_ok'] for r in fetched] == [True] and fetched[0]['path'].startswith(f'/api/agents/{SLUG}/download'), fetched
        assert slugs(cli) == ['main', SLUG], slugs(cli)
        extra, members = compare(archive, cli / '.thclaws/bots' / SLUG)
        assert not extra, extra
        model = installed_identity(cli / '.thclaws/bots' / SLUG, source_settings, Catalogue.agent_uuid)
        assert model == DEFAULT_MODEL != source_settings['model'], \
            f'installed model {model!r}: seed_workspace_model no longer replaces an unreachable pinned model with the AppConfig default (DESIGN §4 M18b 5)'

        # 5: the host root model wins over the package's pinned model when the pinned provider has no credential.
        pinned = base / 'host-pinned'
        scaffold_host(pinned, cloud_url, model=HOST_MODEL)
        result, _ = bots_add(pinned)
        assert result.returncode == 0, result.stdout + result.stderr
        model = installed_identity(pinned / '.thclaws/bots' / SLUG, source_settings, Catalogue.agent_uuid)
        assert model == HOST_MODEL, f'installed model {model!r}, expected the host root model {HOST_MODEL!r} (DESIGN §4 M18b 5)'

        # 6: host API install, supervisor spawns the bot, /v1/agent/info?bot= reaches it.
        hosted = base / 'host'
        scaffold_host(hosted, cloud_url)
        bot_dir = hosted / '.thclaws/bots' / SLUG
        port = secrets.choice(range(20000, 60000))
        serve_log = base / 'host-serve.log'
        with serve_log.open('w') as log:
            host = subprocess.Popen([binary, '--serve', '--port', str(port)], cwd=hosted, env=env, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 60
            while True:
                assert host.poll() is None, 'host --serve exited: ' + serve_log.read_text()[-800:]
                try:
                    if call(port, 'GET', '/healthz', timeout=2)[0] == 200:
                        break
                except (OSError, http.client.HTTPException):
                    pass
                assert time.monotonic() < deadline, 'host /healthz readiness timed out'
                time.sleep(0.3)
            seen = len(Catalogue.requests)
            status, body = call(port, 'POST', '/bots', {'slug': SLUG}, token=api_token, timeout=120)
            assert status == 200 and body.get('ok') and body.get('newly_registered') and body.get('slug') == SLUG, (status, body)
            assert Path(body['dir']).resolve() == bot_dir.resolve(), body['dir']
            assert [r['token_ok'] for r in Catalogue.requests[seen:]] == [True], Catalogue.requests[seen:]
            assert slugs(hosted) == ['main', SLUG], slugs(hosted)
            status, body = call(port, 'GET', '/bots', token=api_token)
            assert status == 200 and SLUG in {b['slug'] for b in body['bots']}, (status, body)
            deadline = time.monotonic() + 90
            while True:
                try:
                    status, info = call(port, 'GET', f'/v1/agent/info?bot={SLUG}', token=api_token, timeout=30)
                except (OSError, http.client.HTTPException):
                    status, info = 0, None
                if status == 200 and isinstance(info, dict):
                    break
                assert host.poll() is None and time.monotonic() < deadline, f'bot {SLUG} never answered /v1/agent/info: {status} {info}'
                time.sleep(1)
            assert Path(info['workspace_dir']).resolve() == bot_dir.resolve(), info['workspace_dir']
            skills = {s['name'] for s in info.get('skills', []) if isinstance(s, dict)}
            assert SLUG in skills and not info.get('mcp_servers'), (skills, info.get('mcp_servers'))
            extra, members = compare(archive, bot_dir)
            assert all(e.startswith('.thclaws/state/') for e in extra), f'serving added files outside .thclaws/state/: {extra}'
            installed_identity(bot_dir, source_settings, Catalogue.agent_uuid)
        finally:
            stop(host)
    finally:
        server.shutdown()
    print(f'M18b native PASS: {lines[0]} revision {revision} (desktop build) packs {members} files, refuses a host without agents '
          f'(after extracting), installs {SLUG} from a bearer-gated catalogue via bots add and POST /bots with only '
          f'{IDENTITY_FILES} rewritten, replaces pinned model {source_settings["model"]} with {DEFAULT_MODEL} / {HOST_MODEL}, '
          f'and serves the bot at .thclaws/bots/{SLUG} with its skill')
    return 0


def main():
    try:
        with tempfile.TemporaryDirectory(prefix='forge-m18b-') as directory:
            base = Path(directory).resolve()
            package, settings = offline(base)
            return native(base, package, settings)
    except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'M18b FAIL: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
