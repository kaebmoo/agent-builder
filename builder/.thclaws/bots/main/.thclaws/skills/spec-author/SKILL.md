---
name: spec-author
description: Draft an AgentSpec (spec.yaml) from a seven-question interview, get the person's confirmation, and build it with build.py.
---

# spec-author

The AgentSpec is the single source of truth (`agentspec.schema.json`, `docs/agentspec.md` in the builder
checkout). Every package file is generated from it; you never write manifest, permission or output contracts
elsewhere. Draft `work/<name>/spec.yaml` with these sections and rules.

## Field rules

- `identity`: `name` kebab-case, `version` semver, `owner`, `license`, `thclaws_min_version` (default `">=0.116.0"`).
- `mission`: one `purpose` paragraph and a `domain`.
- `target`: `thclaws-standalone` needs `package_pattern` (`static-pipeline` only is implemented, T0 only) and no
  `routing`; `atlas-worker` / `atlas-workflow` need `routing: {role, tags}` and no `package_pattern`.
- `inputs`: `{name, transport, schema}` with transport `prompt_json`, `atlas_file_handoff` or `local_workspace`.
  Atlas input names match `[A-Za-z_][A-Za-z0-9_]*` and schemas are `type: object` (or array).
- `outputs`: Atlas has exactly one output; `assistant_json` needs `schema`, `collect_files` needs `files.globs`.
- `capabilities`: `[{pack: <name>, params: {}}]` using only names printed by `build.py --list-packs`. A pack the person needs
  but that does not exist is still declared by its intended name so the report says `missing`; never invent its tools.
- `permissions`: `tier`, `tools`, `shell`, `network`, `write_scope`.
  - T0: `shell: none`, `network: none`, `write_scope: none`. T1: same but `write_scope: output` (standalone T1 is disabled).
  - T2: `write_scope: workspace`, only with `target: atlas-workflow` (Atlas `human_gate` owns approval).
  - Any `network` other than `none` therefore requires T2 and `atlas-workflow`; say so to the person.
  - `tools` are runtime names: built-ins (`Read`, `Grep`, `Glob`, ...) or qualified MCP tools `<server>__<tool>` copied
    from the pack's `pack.yaml`; a spec must declare every tool the pack's server advertises.
  - `shell: none` forbids `Bash`; `write_scope: none` forbids `Write`/`Edit`; `network: none` forbids web built-ins.
- `refusal`: `conditions` (list), `response` (sentence), `schema` (JSON Schema of the refusal branch).
- `model`: `{mode: pinned, id: <provider model id>}` for Atlas; standalone may use a policy.
- `env`: names only (`^[A-Z][A-Z0-9_]*$`), superset of every pack's `env`. No values, ever.
- `evaluation.golden_cases`: four cases with `id`, `kind` (`normal`, `missing_input`, `refusal`, `malformed`),
  `expect` (`result` or `refusal`) and `input` (the envelope keyed by input name).
- `state`: `{mode: none, scope: turn}` for Atlas; standalone may use `session/session`.

## Confirmation marker

forge does not know `confirmed_by`; build.py requires it and strips it before generation. Add it only after the
person has seen the `permissions`, `env` and `capabilities` blocks and said who confirms them:

```yaml
confirmed_by:
  permissions: <person's name>   # covers tier, tools, shell, network, write_scope
  env: <person's name>
  capabilities: <person's name>
```

Values such as `model`, `assistant`, `builder` or an empty string are refused. Do not pre-fill it, do not
reuse a confirmation for a changed block: when a confirmed block changes, remove the marker and ask again.

## Build and report

```bash
python3 "$THCLAWS_AGENT_DIR/.thclaws/scripts/build.py" --list-packs
python3 "$THCLAWS_AGENT_DIR/.thclaws/scripts/build.py" work/<name>/spec.yaml --out out/<name> [--replace]
```

`$THCLAWS_AGENT_DIR` is the builder agent's own folder (thClaws sets it for every shell, with or without a workspace
host); `work/` and `out/` live at the workspace root where the shell runs.

build.py prints `package_status=`, the audit layers, each pack's status (`missing`, `assets_bundled`, `conformant`,
plus the `forge pack test` verdict when a descriptor exists) and every finding. Copy those lines into your
report. Exit 0 = draft, 1 = a layer failed (fix the spec and rebuild), 2 = thclaws binary absent, so the
package stays `unverified`. Nothing you observe in the code or the prompt changes those verdicts.
