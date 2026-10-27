#!/usr/bin/env python3
"""M10: pack authoring workflow — scaffold, discover, conformance harness boundaries, echo pack end to end.

Exit 0 = all checks incl. native `thclaws agent validate` draft; 1 = failure; 2 = offline checks passed but
thclaws is absent, so draft promotion of the echo package is not verified.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import yaml

    from forge.audit import audit_package, exit_code
    from forge.generate import generate, render
    from forge.pack_author import discover, scaffold_pack
    from forge.pack_test import test_pack
    from forge.packs import conformance, read_pack, validate_pack
    from forge.spec import load_spec
except ModuleNotFoundError:
    print("M10 FAIL: install project dependencies from pyproject.toml first")
    raise SystemExit(1)

ECHO = ROOT / 'fixtures/packs/echo'
SERVER = (ECHO / 'scripts/server.py').read_text(encoding='utf-8')
CLI = [sys.executable, '-m', 'forge.cli', 'pack']


def variant(source: str, old: str, new: str) -> str:
    assert old in source, f'variant anchor missing: {old!r}'
    return source.replace(old, new)


def fill(descriptor: dict, **changes) -> dict:
    filled = copy.deepcopy(descriptor)
    filled.update({'min_tier': 'T0', 'network': 'none', 'scripts': ['server.py']})
    filled['mcp_servers'][0]['args'] = ['scripts/server.py']
    filled['fixture'] = {'setup': 'fixture/setup.py', 'cases': [
        {'tool': 'echo__echo', 'args': {'text': 'hello'}, 'expect': 'ok'},
        {'tool': 'echo__echo', 'args': {}, 'expect': 'error'}]}
    filled.update(changes)
    return filled


def expect_failure(packs: Path, name: str, reason: str) -> None:
    report, code = test_pack(name, packs, timeout=3)
    assert code == 1 and report['status'] == 'failed' and reason in report['detail'], (name, reason, report)


def check(work: Path) -> int:
    packs = work / 'packs'
    packs.mkdir()

    # 1. Scaffold: contract-shaped, explicit TODOs, refuses to overwrite, rejects names outside the charset.
    created = scaffold_pack('echo', packs)
    folder = packs / 'echo'
    assert created == ['README.md', 'fixture/setup.py', 'pack.yaml'] and (folder / 'scripts').is_dir(), created
    scaffold = yaml.safe_load((folder / 'pack.yaml').read_text())
    assert scaffold['min_tier'] == 'TODO' and scaffold['mcp_servers'][0]['tools'] == ['TODO'], scaffold
    expect_failure(packs, 'echo', 'invalid min_tier or network')
    for bad in ('Echo', 'echo_server', '-echo', ''):
        try:
            scaffold_pack(bad, packs)
        except ValueError:
            pass
        else:
            raise AssertionError(f'scaffold accepted invalid name {bad!r}')
    try:
        scaffold_pack('echo', packs)
    except FileExistsError:
        pass
    else:
        raise AssertionError('scaffold overwrote an existing pack')

    # 2. Implement: copy the reference server, fill the author decisions, run the harness end to end (no LLM).
    (folder / 'scripts/server.py').write_text(SERVER)
    implemented = fill(scaffold)
    implemented['mcp_servers'][0]['tools'] = ['echo', 'reverse']
    (folder / 'pack.yaml').write_text(yaml.safe_dump(implemented))
    evidence, code = test_pack('echo', packs, timeout=5)
    assert code == 0 and evidence['status'] == 'conformant', evidence
    assert conformance(folder, read_pack(folder)) == evidence
    assert evidence['servers']['echo']['tools'] == ['echo', 'reverse'] and len(evidence['cases']) == 2
    repeat, code = test_pack('echo', packs, timeout=5)
    assert code == 0 and repeat == evidence, 'conformance evidence is not deterministic'
    # The scaffold's untouched setup.py (prints {}) is valid because echo declares no env; keep it.

    # 3. The checked-in reference pack is itself conformant and matches what the workflow produces.
    reference_dir = work / 'reference'
    shutil.copytree(ECHO, reference_dir / 'echo', ignore=shutil.ignore_patterns('pack-conformance.json', '__pycache__'))
    reference_evidence, code = test_pack('echo', reference_dir, timeout=5)
    assert code == 0 and len(reference_evidence['cases']) == 5, reference_evidence
    assert reference_evidence['servers']['echo']['tools_digest'] == evidence['servers']['echo']['tools_digest']
    reference = read_pack(ECHO)
    assert {k: reference[k] for k in ('min_tier', 'network', 'hosts', 'env', 'tools', 'mcp_servers', 'scripts', 'skills')} == {
        k: implemented[k] for k in ('min_tier', 'network', 'hosts', 'env', 'tools', 'mcp_servers', 'scripts', 'skills')}

    # 4. Discover: complete tool inventory from the live server, deterministic, author decisions left as TODO.
    draft = discover('echo', sys.executable, [str(ECHO / 'scripts/server.py')], [], timeout=5)
    assert draft == discover('echo', sys.executable, [str(ECHO / 'scripts/server.py')], [], timeout=5), 'draft not deterministic'
    drafted = yaml.safe_load(draft)
    assert drafted['mcp_servers'][0]['tools'] == ['echo', 'reverse'] and drafted['mcp_servers'][0]['mutating'] == []
    assert drafted['min_tier'] == 'TODO' and drafted['network'] == 'TODO' and drafted['fixture']['cases'] == []
    assert evidence['servers']['echo']['tools_digest'] in draft, 'draft must record the tools/list digest'
    assert set(drafted) == set(implemented), 'draft is not contract v2 shaped'
    discovered = packs / 'discovered'
    discovered.mkdir()
    (discovered / 'pack.yaml').write_text(draft)
    expect_failure(packs, 'discovered', 'invalid min_tier or network')
    (discovered / 'scripts').mkdir()
    (discovered / 'scripts/server.py').write_text(SERVER)
    shutil.copy(folder / 'fixture/setup.py', (discovered / 'fixture').mkdir() or discovered / 'fixture/setup.py')
    drafted['mcp_servers'][0]['name'] = 'discovered'
    completed = fill(drafted)
    completed['fixture']['cases'] = [{**c, 'tool': c['tool'].replace('echo__', 'discovered__')} for c in completed['fixture']['cases']]
    (discovered / 'pack.yaml').write_text(yaml.safe_dump(completed))
    report, code = test_pack('discovered', packs, timeout=5)
    assert code == 0, report

    # Annotations only propose mutating; a T0 descriptor that keeps the proposal is rejected by the contract.
    destructive = work / 'destructive.py'
    destructive.write_text(variant(SERVER, "'reverse', 'description': 'Return the supplied text reversed.',",
                                   "'reverse', 'description': 'Return the supplied text reversed.',")
                           .replace("{'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},\n]",
                                    "{'readOnlyHint': False, 'destructiveHint': True, 'openWorldHint': False}},\n]"))
    proposal = yaml.safe_load(discover('echo', sys.executable, [str(destructive)], [], timeout=5))
    assert proposal['mcp_servers'][0]['mutating'] == ['reverse'] and proposal['min_tier'] == 'TODO', proposal
    try:
        validate_pack(fill(proposal), folder)
    except ValueError as exc:
        assert 'T2' in str(exc), exc
    else:
        raise AssertionError('mutating tool accepted at min_tier T0')
    unannotated = work / 'unannotated.py'
    unannotated.write_text(SERVER.replace("     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},\n", "},\n"))
    plain = discover('echo', sys.executable, [str(unannotated)], [], timeout=5)
    assert yaml.safe_load(plain)['mcp_servers'][0]['mutating'] == [] and 'no annotations advertised' in plain

    # Discover rejects what the contract can never accept, and never passes undeclared env.
    for name in ('Echo', 'echo_server'):
        try:
            discover(name, sys.executable, [str(ECHO / 'scripts/server.py')], [], timeout=5)
        except ValueError as exc:
            assert 'server name' in str(exc), exc
        else:
            raise AssertionError(f'discover accepted server name {name!r}')
    bad_tool = work / 'bad_tool.py'
    bad_tool.write_text(variant(SERVER, "{'name': 'reverse',", "{'name': 'Reverse-It',"))
    try:
        discover('echo', sys.executable, [str(bad_tool)], [], timeout=5)
    except ValueError as exc:
        assert 'charset' in str(exc), exc
    else:
        raise AssertionError('discover accepted a tool name outside the contract charset')
    os.environ['FORGE_M10_UNDECLARED'] = 'synthetic-must-not-leak'
    leak_probe = work / 'leak.py'
    leak_probe.write_text("import os, sys\nassert 'FORGE_M10_UNDECLARED' not in os.environ\n" + SERVER.replace('import json\nimport sys\n', 'import json\n'))
    assert yaml.safe_load(discover('echo', sys.executable, [str(leak_probe)], [], timeout=5))['mcp_servers'][0]['tools'] == ['echo', 'reverse']
    try:
        discover('echo', sys.executable, [str(ECHO / 'scripts/server.py')], ['FORGE_M10_MISSING_NAME'], timeout=5)
    except ValueError as exc:
        assert 'not set' in str(exc), exc
    else:
        raise AssertionError('discover accepted an unset env name')
    try:
        discover('echo', 'forge-nonexistent-runtime', [], [], timeout=5)
    except ValueError as exc:
        assert 'runtime not found' in str(exc), exc
    else:
        raise AssertionError('discover accepted a missing runtime')

    # 5. Harness boundaries on the implemented echo pack: clean env, inventory equality, charset, T0 mutating.
    def save_server(text: str) -> None:
        (folder / 'scripts/server.py').write_text(text)

    save_server("import os, sys\nos.environ['FORGE_M10_UNDECLARED']\n" + SERVER.replace('import json\nimport sys\n', 'import json\n'))
    expect_failure(packs, 'echo', 'only PATH and the env names declared in pack.yaml')
    save_server(SERVER.replace("    if method == 'tools/list':\n        return {'tools': TOOLS}",
                               "    if method == 'tools/list':\n        return {'tools': TOOLS + [dict(TOOLS[0], name='admin')]}"))
    expect_failure(packs, 'echo', 'differs from declared tools')
    save_server(SERVER.replace("        return {'tools': TOOLS}", "        return {'tools': TOOLS[:1]}"))
    expect_failure(packs, 'echo', 'differs from declared tools')
    save_server(SERVER.replace("        return {'tools': TOOLS}", "        return {'tools': TOOLS, 'nextCursor': 'page2'}"))
    expect_failure(packs, 'echo', 'paginated')
    save_server(SERVER.replace("        raise ValueError('expected exactly one argument: text')", "        arguments = {'text': 'x'}"))
    expect_failure(packs, 'echo', 'expected error, got ok')
    save_server(SERVER)
    for change, reason in [({'name': 'Echo'}, 'server name'), ({'name': 'echo_server'}, 'server name'),
                           ({'tools': ['echo', 'Reverse']}, 'invalid name'), ({'tools': ['echo', 'echo__reverse']}, 'without __'),
                           ({'mutating': ['echo']}, 'T2')]:
        broken = copy.deepcopy(implemented)
        broken['mcp_servers'][0].update(change)
        try:
            validate_pack(broken, folder)
        except ValueError as exc:
            assert reason in str(exc), (change, exc)
        else:
            raise AssertionError(f'contract accepted {change}')
    evidence, code = test_pack('echo', packs, timeout=5)
    assert code == 0, evidence

    # 6. CLI surface: new / discover / test with --packs-dir, exit codes 0 and 1, refusal to overwrite.
    cli_packs = work / 'cli-packs'
    cli_packs.mkdir()
    def run(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([*CLI, *argv], cwd=ROOT, capture_output=True, text=True, check=False)
    assert run('new', 'cli-echo', '--packs-dir', str(cli_packs)).returncode == 0
    assert run('new', 'cli-echo', '--packs-dir', str(cli_packs)).returncode == 1
    untouched = run('test', 'cli-echo', '--packs-dir', str(cli_packs))
    assert untouched.returncode == 1 and 'invalid min_tier' in untouched.stdout, untouched.stdout
    out_file = cli_packs / 'draft.yaml'
    drafted_cli = run('discover', '--name', 'echo', '--command', sys.executable, '--out', str(out_file),
                      '--args', str(ECHO / 'scripts/server.py'))
    assert drafted_cli.returncode == 0 and out_file.read_text() == draft, drafted_cli.stdout + drafted_cli.stderr
    assert run('discover', '--name', 'echo', '--command', sys.executable, '--out', str(out_file),
               '--args', str(ECHO / 'scripts/server.py')).returncode == 1, 'discover overwrote --out'
    missing_runtime = run('discover', '--name', 'echo', '--command', 'forge-nonexistent-runtime')
    assert missing_runtime.returncode == 1 and 'runtime not found' in missing_runtime.stderr
    assert run('test', 'echo', '--packs-dir', str(packs)).returncode == 0

    # 7. Spec that uses the echo pack: mcp.json without secrets, static audit passes, dependency conformant.
    spec = load_spec(ECHO / 'spec.yaml')
    files, build, problems = render(spec, packs)
    assert not problems and build['dependencies'] == [{
        'pack': 'echo', 'status': 'conformant', 'mcp_servers': ['echo'], 'network': 'none', 'hosts': [], 'env': [],
        'conformance': evidence}], build['dependencies']
    assert json.loads(files['.thclaws/mcp.json']) == {'mcpServers': {'echo': {'command': 'python3', 'args': ['.thclaws/scripts/echo--server.py']}}}
    assert files['.thclaws/scripts/echo--server.py'] == SERVER.encode()
    assert b'synthetic-must-not-leak' not in b''.join(files.values()) and b'FORGE_M10' not in b''.join(files.values())
    package = work / 'echo-reader'
    generate(spec, package, packs_dir=packs)
    offline = audit_package(package, packs_dir=packs, thclaws=False)
    assert offline['audit']['static'] == 'passed' and not offline['static_audit']['findings'], offline['static_audit']
    underdeclared = copy.deepcopy(spec)
    underdeclared['permissions']['tools'] = ['echo__echo']
    assert any('required tools' in p for p in render(underdeclared, packs)[2]), 'spec may not omit an advertised tool'
    (folder / 'pack-conformance.json').unlink()
    stale = audit_package(package, packs_dir=packs, thclaws=False)
    assert stale['audit']['static'] == 'passed' and stale['dependencies'][0]['status'] == 'assets_bundled'
    (folder / 'pack-conformance.json').write_text(json.dumps({**evidence, 'status': 'failed'}))
    failed = audit_package(package, packs_dir=packs, thclaws=False)
    assert failed['audit']['static'] == 'failed' and any('rerun forge pack test' in f['message'] for f in failed['static_audit']['findings'])
    test_pack('echo', packs, timeout=5)

    # 8. Authoring material exists and points at the contract.
    # builder/ is a host workspace since M13; its agent (and the skill) live in .thclaws/bots/main (DESIGN §9).
    skill = (ROOT / 'builder/.thclaws/bots/main/.thclaws/skills/pack-author/SKILL.md').read_text(encoding='utf-8')
    assert skill.startswith('---\nname: pack-author\n') and 'forge pack test' in skill and 'docs/packs.md' in skill
    guide = (ROOT / 'docs/packs.md').read_text(encoding='utf-8')
    for phrase in ('forge pack new', 'forge pack discover', 'forge pack test', 'fixtures/packs/echo', 'dry_run', 'idempotency_key'):
        assert phrase in guide, phrase
    print('M10 offline PASS: scaffold, discover, clean-env/inventory/charset/tier boundaries, CLI, echo package static audit')

    native = audit_package(package, packs_dir=packs)
    assert exit_code(native) != 1, native['static_audit']['thclaws_validate']
    if exit_code(native) == 2:
        print('M10 SKIP: thclaws not on PATH; echo package stays unverified, draft promotion not checked (exit 2)')
        return 2
    assert native['package_status'] == 'draft', native['package_status']
    print(f"M10 PASS: echo package is draft on {native['static_audit']['thclaws_validate']['version']}")
    return 0


if __name__ == '__main__':
    try:
        with tempfile.TemporaryDirectory(prefix='forge-m10-') as temporary:
            raise SystemExit(check(Path(temporary).resolve()))
    except (AssertionError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError, yaml.YAMLError) as exc:
        print(f'M10 FAIL: {type(exc).__name__}: {exc}')
        raise SystemExit(1)
