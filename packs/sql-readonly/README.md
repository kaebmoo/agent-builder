# SQL pack (M7a)

Narrow adapter around `kaebmoo/AI`'s `mcp_servers.nt_query_mcp` at
`d95dc28628673d7adef1d6f1958116922fea1946`. It reuses `get_table_stats`,
`validate_sql` (the shared ValidationService), and `execute_query` without launching
upstream's full MCP surface. Only `metadata`, `query`, `validate` are advertised.
No upstream source or business data is copied into this repository.

## Run

From the agent-builder checkout, with Python 3.11+:

```bash
pip install -e '.[sql-pack]'
python3 -m forge.cli pack test sql-readonly
python3 -m forge.cli generate fixtures/sql-reader/spec.yaml --out out/sql-reader
python3 -m forge.cli audit out/sql-reader --write
```

The fixture expects the AI source checkout beside agent-builder (`../AI`) at the
revision above. It creates three synthetic invoices in the harness's temporary
directory and prints a JSON object containing exactly the declared env names.
The harness deletes the database afterward. No provider key or real database is used.
An absent source checkout or MCP Python dependency produces explicit skip exit 2.

`python3` on PATH must be the interpreter with the MCP dependency installed.
The builder's harness uses stdlib JSON-RPC plus the project's existing JSON Schema validator;
`mcp` is an optional dependency of this external-runtime adapter only.

Exit codes: 0 = conformant; 1 = descriptor/protocol/case failure; 2 = missing runtime.
`pack-conformance.json` is local ignored evidence: descriptor/assets digest,
names/input-schema digest, and per-case verdicts. It stores no env values or query
result content. A failed/skipped/stale test blocks reuse of that evidence.
When tools/list changes without a pack change, repeated tests fail until the owner
reviews and updates pack.yaml. Evidence records the last test, not a continuous
attestation of an installed runtime.

## Deploy

Provision the AI source and MCP Python runtime externally. Set daemon env:

- `SQL_READONLY_AI_ROOT`: absolute path to the reviewed AI checkout.
- `SQL_READONLY_DSN`: `sqlite:///` followed by an absolute SQLite file path.

Generated `.thclaws/mcp.json` contains command/args only. Start a dedicated daemon
with CWD set to the generated package, so thClaws loads that config at startup.
The adapter explicitly rejects other database engines, inherits upstream SQLite
`mode=ro`, adds a SQLite authorizer restricting operations to reads, and never
exposes `validate_first` or arbitrary connection arguments. Query rows are capped at 100.
Metadata uses the same restricted connection. Mutations fail at the server.

This does not make `/agent/run` read-only: built-in Bash/Write/Edit remain available.
Static audit can earn `draft`; agent tool-call evidence and `candidate` require M6.
The setup and MCP scripts are executable pack code; the harness isolates their env
and working directory, but is not an OS/network sandbox. Review packs before running them.
