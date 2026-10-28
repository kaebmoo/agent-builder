#!/usr/bin/env python3
"""M15: Atlas packages declare allowedTools, so /agent/run enforces the built-in tool allowlist — proven on the real package.

Exit 0 = offline checks, native /agent/run probes against the generator's own package and the provider golden cases all
passed; 1 = failure (including a binary that is not the 0.138.0 baseline: fail closed); 2 = offline passed but thclaws is
absent, the binary is a CLI-only build without --serve, or the provider key is absent, so that part was explicitly skipped.

Scope (DESIGN §4 "Tool allowlist บน /agent/run"): the filter covers the built-ins thClaws registers before it.
Skill/SkillList/SkillSearch and MCP tools register afterwards and stay outside the claim; the native part records that limit
instead of assuming it, and shows that the operator's user layer cannot switch the late HAL/media groups back on.
"""
from __future__ import annotations

import argparse
import copy
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
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from forge.audit import audit_package, exit_code, write_report
from forge.generate import LATE_BUILTIN_TOOLS, THCLAWS_BASELINE, generate, render
from forge.live_test import (
    COPY_IGNORE,
    check_layout,
    check_servers,
    isolated_env,
    live_audit,
    request,
    tree_snapshot,
)
from forge.pack_test import test_pack
from forge.spec import load_spec

ECHO = ROOT / 'fixtures/packs/echo'
ATLAS_FIXTURES = ('invoice-reviewer', 'sql-reader', 'publisher')
STANDALONE_FIXTURES = ('text-reviewer-standalone', 'note-writer-standalone')
SKILL_TOOLS = {'Skill', 'SkillList', 'SkillSearch'}
MCP = {'echo__echo', 'echo__reverse'}
PINS = {'browserEnabled': False, 'halEnabled': False, 'imageToolsEnabled': False}


def rejects(call, needle=None):
    try:
        call()
    except ValueError as exc:
        assert needle is None or needle in str(exc), (needle, str(exc))
        return
    raise AssertionError('expected a ValueError')


def matrix(report, claim):
    return next(row for row in report['guarantee_matrix'] if row['claim'] == claim)


def probe_spec():
    """The echo fixture plus Read: one package whose allowlist holds a built-in and two MCP tools."""
    spec = load_spec(ECHO / 'spec.yaml')
    spec['permissions']['tools'] = ['Read', *spec['permissions']['tools']]
    return spec


def offline(base, packs):
    spec = probe_spec()
    package = base / 'package'
    generate(spec, package, packs_dir=packs)
    generate(spec, base / 'repeat', packs_dir=packs)
    assert tree_snapshot(package) == tree_snapshot(base / 'repeat'), 'Atlas generation is not deterministic'
    settings = json.loads((package / '.thclaws/settings.json').read_text(encoding='utf-8'))
    assert settings['allowedTools'] == ['Read', 'echo__echo', 'echo__reverse'], settings
    assert {key: settings[key] for key in PINS} == PINS, settings
    for name in ATLAS_FIXTURES:
        fixture = load_spec(ROOT / f'fixtures/{name}/spec.yaml')
        rendered = json.loads(render(fixture)[0]['.thclaws/settings.json'])
        assert rendered['allowedTools'] == sorted(fixture['permissions']['tools']), (name, rendered)
        assert {key: rendered[key] for key in PINS} == PINS, (name, rendered)
    for name in STANDALONE_FIXTURES:
        rendered = json.loads(render(load_spec(ROOT / f'fixtures/{name}/spec.yaml'))[0]['.thclaws/settings.json'])
        assert not {'allowedTools', *PINS} & set(rendered), (name, 'standalone settings must not change in M15')

    # Built-ins registered after the filter are pinned off, so an Atlas spec that declares one is rejected, not shipped.
    # T2 is the only tier that may declare a shell or network, so it also reaches the rule for the HAL (network) tools.
    t2 = copy.deepcopy(spec)
    t2['target'] = 'atlas-workflow'
    t2['permissions'].update(tier='T2', shell='sandboxed', network='general-http', write_scope='workspace',
                             tools=['Bash', *spec['permissions']['tools']])
    for tool in sorted(LATE_BUILTIN_TOOLS):
        invalid = copy.deepcopy(t2)
        invalid['permissions']['tools'].append(tool)
        rejects(lambda invalid=invalid: render(invalid, packs), 'registered after allowedTools')

    report = audit_package(package, packs_dir=packs, thclaws=False)
    assert exit_code(report) != 1, report['static_audit']
    allowlist = matrix(report, 'tool_allowlist')
    assert allowlist['status'] == 'Enforced', allowlist
    for limit in ('Skill/SkillList/SkillSearch and MCP tools register after that filter', 'CWD=package', 'THCLAWS_PROJECT_ROOT'):
        assert limit in allowlist['basis'], (limit, allowlist['basis'])
    assert matrix(report, 'no_shell')['status'] == 'Enforced'
    assert matrix(report, 'no_file_writes')['status'] == 'Not guaranteed'
    assert matrix(render(t2, packs)[1], 'no_shell')['status'] == 'Not applicable'
    standalone = render(load_spec(ROOT / 'fixtures/text-reviewer-standalone/spec.yaml'))[1]
    assert 'Native named subagents' in matrix(standalone, 'tool_allowlist')['basis'], 'standalone rows must not change'
    assert any('allowedTools' in hint and 'THCLAWS_PROJECT_ROOT' in hint for hint in report['deployment_hints'])

    # Drift: the allowlist and the pins are part of the rendered contract, so weakening either in place fails the audit.
    path = package / '.thclaws/settings.json'
    original = path.read_text(encoding='utf-8')
    for mutation in ({k: v for k, v in settings.items() if k != 'allowedTools'},
                     {**settings, 'allowedTools': [*settings['allowedTools'], 'Bash']},
                     {**settings, 'halEnabled': True},
                     {k: v for k, v in settings.items() if k != 'browserEnabled'}):
        path.write_text(json.dumps(mutation, indent=2) + '\n', encoding='utf-8')
        findings = audit_package(package, packs_dir=packs, thclaws=False)['static_audit']['findings']
        assert any(f['rule'] == 'drift' and '.thclaws/settings.json' in f['message'] for f in findings), (mutation, findings)
    path.write_text(original, encoding='utf-8')
    print('M15 offline PASS: deterministic allowedTools + browser/HAL/media pins on every Atlas fixture, standalone '
          'unchanged, late built-ins rejected, matrix rows and limits, deployment hint, settings drift')
    return spec


class Provider(BaseHTTPRequestHandler):
    """Scripted OpenAI-compatible provider: forces one tool call, then answers. Records what thClaws offered and replied."""
    records: ClassVar[list] = []
    action = None  # (tool, arguments) forced on the first turn

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.records.append(body)
        if self.action and not any(m['role'] == 'tool' for m in body['messages']):
            name, arguments = self.action
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'probe-call', 'type': 'function',
                     'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
            finish = 'tool_calls'
        else:
            delta, finish = {'role': 'assistant', 'content': json.dumps({'refused': True, 'reason': 'probe'})}, 'stop'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for chunk, end in [(delta, None), ({}, finish)]:
            self.wfile.write(('data: ' + json.dumps({'id': 'probe', 'object': 'chat.completion.chunk', 'model': 'm15-probe',
                                                     'choices': [{'index': 0, 'delta': chunk, 'finish_reason': end}]}) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')


def binary_pair(binary):
    version = subprocess.run([binary, '--version'], capture_output=True, text=True, check=True, timeout=30)
    print(version.stdout.strip())
    lines = [line.strip() for line in version.stdout.splitlines() if line.strip()]
    return lines[0].removeprefix('thclaws ') if lines else ''


def desktop_build(binary):
    """Only a gui-feature build has --serve; it is also the only one that lists `bots` (DESIGN §4 host layout 13)."""
    result = subprocess.run([binary, '--help'], capture_output=True, text=True, timeout=60, check=False)
    return result.returncode == 0 and re.search(r'^\s+bots\s', result.stdout, re.MULTILINE) is not None


def native(base, spec, packs, binary):
    runtime = binary_pair(binary)
    assert runtime == THCLAWS_BASELINE, f'thclaws {runtime} is not the {THCLAWS_BASELINE} baseline; re-check DESIGN §4 "Tool allowlist" first'
    package = base / 'native'
    probe = copy.deepcopy(spec)
    probe['model']['id'] = 'oai/m15-probe'
    generate(probe, package, packs_dir=packs)
    with tempfile.TemporaryDirectory(prefix='m15-pycache-') as cache:
        validate = subprocess.run([binary, 'agent', 'validate', str(package)], capture_output=True, text=True,
                                  env={**os.environ, 'PYTHONPYCACHEPREFIX': cache}, timeout=120, check=False)
    assert validate.returncode == 0 and 'validates' in validate.stdout + validate.stderr, validate.stdout + validate.stderr
    work = base / 'served'
    shutil.copytree(package, work, ignore=COPY_IGNORE)
    settings_path = work / '.thclaws/settings.json'
    generated = json.loads(settings_path.read_text(encoding='utf-8'))
    home = base / 'home'
    home.mkdir()
    # The operator's user layer switches every late group on; only the package pins may keep them away.
    user = base / 'user-settings.json'
    user.write_text(json.dumps({'browserEnabled': False, 'halEnabled': True, 'imageToolsEnabled': True}))
    with ThreadingHTTPServer(('127.0.0.1', 0), Provider) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        token = secrets.token_urlsafe(32)
        env = isolated_env(home, user, token, OPENAI_COMPAT_API_KEY='local-probe-only', HAL_API_KEY='local-probe-only', GEMINI_API_KEY='local-probe-only',
                           OPENAI_COMPAT_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen([binary, '--serve', '--port', str(port)], cwd=work, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.monotonic() + 30
            while True:
                assert process.poll() is None, 'daemon exited before readiness'
                try:
                    info = request(port, token, '/v1/agent/info', 1)
                    break
                except (OSError, ValueError):
                    assert time.monotonic() < deadline, 'daemon readiness timed out'
                    time.sleep(0.1)
            check_layout(info, work)
            check_servers(info, {'echo'}, {})

            def run(tool, arguments):
                """One /agent/run: (tools offered to the model, SSE tool result of the forced call, files the run changed)."""
                Provider.records.clear()
                Provider.action = (tool, arguments)
                before = tree_snapshot(work)
                events = request(port, token, '/agent/run', 60, {'prompt': 'tool allowlist probe', 'workspace_dir': str(work),
                                                                  'model': 'oai/m15-probe', 'stream': True})
                offered = [{t['function']['name'] for t in r.get('tools', [])} for r in Provider.records]
                assert offered and all(o == offered[0] for o in offered), 'the tool list changed within one run'
                result = next(e['data'] for e in events if e['event'] == 'tool_use_result' and e['data'].get('id') == 'probe-call')
                replies = [m['content'] for r in Provider.records for m in r['messages'] if m['role'] == 'tool']
                assert replies, (tool, 'provider never received the tool result; absence alone is not denial evidence')
                after = tree_snapshot(work)
                return offered[0], result, sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))

            def configure(value):
                settings_path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')

            # 1. Generated settings: the model sees the allowlist plus the Skill tools; every other built-in is gone.
            expected = set(generated['allowedTools']) | SKILL_TOOLS
            for tool, arguments, allowed in (
                ('Read', {'path': 'AGENTS.md'}, True),
                ('echo__echo', {'text': 'allowlist'}, True),
                ('Bash', {'command': 'touch bash-probe.txt'}, False),
                ('Write', {'path': 'write-probe.txt', 'content': 'native probe'}, False),
                ('WebScrape', {'url': 'https://example.test/'}, False),
                ('TextToImage', {'prompt': 'native probe'}, False),
            ):
                offered, result, changed = run(tool, arguments)
                assert offered == expected, (tool, sorted(offered ^ expected))
                if allowed:
                    assert result['status'] == 'ok', (tool, result)
                else:
                    assert result['status'] == 'error' and f'unknown tool: {tool}' in result['output'], (tool, result)
                    assert not changed, (tool, 'run changed files: ' + ', '.join(changed))
                print(f'M15 native PASS: {tool}: {"ran" if allowed else "unknown tool"}; offered {sorted(offered)}')

            # 2. Limit, recorded natively: an allowlist without the MCP names still leaves every tool registered after the
            #    filter — Skill, MCP and the HAL/media groups. Only the package can switch those groups on: the user layer
            #    above already tried, and step 1 saw none of them (DESIGN §4 "Tool allowlist" 4).
            configure(generated | {'allowedTools': ['Read'], 'halEnabled': True, 'imageToolsEnabled': True})
            offered, result, _ = run('Read', {'path': 'AGENTS.md'})
            late = offered - {'Read'}
            assert late == SKILL_TOOLS | MCP | LATE_BUILTIN_TOOLS, sorted(late ^ (SKILL_TOOLS | MCP | LATE_BUILTIN_TOOLS))
            assert result['status'] == 'ok', result
            print(f'M15 native PASS (limit): allowedTools [Read] still offers {sorted(late)}')

            # 3. Control: without allowedTools the same daemon offers and runs Bash, so the list is what removed it.
            configure({key: value for key, value in generated.items() if key != 'allowedTools'})
            offered, result, changed = run('Bash', {'command': 'touch bash-control.txt'})
            assert {'Bash', 'Write', 'Edit'} <= offered and result['status'] == 'ok' and changed == ['bash-control.txt'], (result, changed)
            (work / 'bash-control.txt').unlink()
            print(f'M15 native PASS (control): no allowedTools -> {len(offered)} tools offered and Bash ran')

            # 4. Settings are read per request: restoring the generated file restores the denial without a restart.
            configure(generated)
            offered, result, changed = run('Bash', {'command': 'touch bash-probe.txt'})
            assert offered == expected and result['status'] == 'error' and not changed, (result, changed)
            print('M15 native PASS: restored allowedTools denies Bash again on the same daemon')
        finally:
            Provider.action = None
            Provider.records.clear()
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
            process.wait(timeout=5)
            server.shutdown()
            thread.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--model', help='explicit provider model for the live fixture; the checked-in fixture is unchanged')
    parser.add_argument('--provider-key-env', default='OPENAI_API_KEY')
    parser.add_argument('--timeout', type=float, default=180)
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix='forge-m15-') as directory:
            base = Path(directory).resolve()
            packs = base / 'packs'
            shutil.copytree(ECHO, packs / 'echo', ignore=shutil.ignore_patterns('pack-conformance.json', '__pycache__'))
            evidence, code = test_pack('echo', packs, timeout=10)
            assert code == 0, evidence
            spec = offline(base, packs)
            binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
            if not binary:
                print('M15 SKIP: thclaws absent; native /agent/run probes and provider live checks not run (exit 2)')
                return 2
            if not desktop_build(binary):
                print('M15 SKIP: CLI-only thclaws build has no --serve; native /agent/run probes not run (exit 2)')
                return 2
            native(base, spec, packs, binary)
            if not os.environ.get(args.provider_key_env, '').strip():
                print(f'M15 SKIP: no provider key in {args.provider_key_env}; live golden cases of the allowlisted package not run (exit 2)')
                return 2
            if args.model:
                spec['model']['id'] = args.model
            package = base / 'live'
            generate(spec, package, packs_dir=packs)
            with patch.dict(os.environ, {'THCLAWS_BIN': binary}):
                report, code = live_audit(package, packs_dir=packs, key_env=args.provider_key_env, timeout=args.timeout)
            write_report(package, report)
            print(f'M15 provider {["PASS", "FAIL", "SKIP"][code]}: {report["live_audit"]["detail"]} '
                  f'called={report["live_audit"]["called_tools"]}')
            for case in report['live_audit']['cases']:
                print(f'  {case["id"]}: {case["status"]} {case["detail"]}')
            return code
    except (AssertionError, ValueError, OSError, StopIteration, subprocess.SubprocessError) as exc:
        print(f'M15 FAIL: {type(exc).__name__}: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
