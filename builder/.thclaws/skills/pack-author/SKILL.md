---
name: pack-author
description: Write or wrap an MCP server as an agent-builder capability pack that passes `forge pack test` without an LLM.
---

# pack-author

You implement the external half of a capability: an MCP server plus its `packs/<name>/pack.yaml` contract
(agent-builder `docs/packs.md`, DESIGN §5). The builder never writes MCP code for you, and you never edit the
builder's schema, generator, audit or templates to make a pack fit.

## Procedure

1. Read `docs/packs.md` and the reference pack `fixtures/packs/echo/` before writing anything.
2. Scaffold with `forge pack new <name>` (new server) or draft with
   `forge pack discover --name <name> --command <cmd> --args <argv...>` (existing server). Both leave `TODO`
   markers; a draft is not a pack until `forge pack test <name>` exits 0.
3. Implement `scripts/server.py` as a stdio JSON-RPC MCP: `initialize` (reply with `protocolVersion`),
   `tools/list` (every tool, one page, valid `inputSchema`), `tools/call` (`content` list plus `isError`).
   Prefer the Python stdlib; any other runtime is a deployment dependency the operator must install, and
   `fixture/setup.py` must exit 2 when that runtime is missing.
4. Validate arguments and reject mutation **at the server**: wrong arguments, mutating requests on a
   read-only pack, path traversal and undeclared destinations return `isError: true`, never an exception
   and never a prompt-level promise.
5. Fill `pack.yaml`: tool names in the contract charset, `tools` equal to the advertised set, `env` as names
   only, `network`/`hosts` as the real minimum, `mutating` only for tools with side effects (then
   `min_tier: T2` and explicit `dry_run` + `idempotency_key` inputs).
6. Write `fixture/setup.py` that builds synthetic data under `argv[1]` with no network and no real data, and
   prints exactly the declared env names as JSON. Add `fixture.cases` with at least one `expect: ok` and one
   `expect: error` per server.
7. Run `forge pack test <name>` and fix every reported reason until it exits 0. Exit 2 means the runtime is
   absent on this machine; say so instead of calling the pack conformant.

## Never

- Put secret values, real data, credentials or `*.db` files anywhere under the pack; only env **names**.
- Read an environment variable the pack does not declare; the harness passes only PATH plus declared names.
- Rename or hide tools to satisfy a spec; the spec declares what the server advertises, not the reverse.
- Mark a tool non-mutating because its annotations say so; prove it with an `expect: error` case.
- Claim a pack is conformant, read-only or deploy-ready from reading code; only the harness verdict counts.
- Decide permissions, network, env, tier or approval for the agent that will use the pack; the AgentSpec
  owner confirms those (`spec-author` skill, DESIGN §9).
