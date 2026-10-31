# SQLite read-only pack

An independent stdio MCP server for reading any SQLite file. It uses Python 3.11+
and the standard library only, with no external repository, provider key, network,
or additional dependency. Any agent with MCP support can use it.

## Run

From the agent-builder checkout:

```bash
forge pack test sqlite-readonly
forge generate fixtures/sqlite-reader/spec.yaml --out /tmp/sqlite-reader
forge audit /tmp/sqlite-reader --security
```

The fixture creates a temporary `demo.sqlite` with three synthetic vendors and six
invoices for August 2026. The harness removes the data afterward and supplies only
PATH and the declared environment variable. No real database is used.

Pack test exit codes: 0 = conformant (all 12 cases); 1 = descriptor, protocol, or
case failure; 2 = missing runtime (`sqlite3` unavailable or `python3` absent from PATH).
The harness writes ignored local `pack-conformance.json` evidence; it contains
digests and verdicts, without environment values or result rows. Changed assets
require a new test. Generate returns 0 on success and 1 on failure; audit returns
0 when its checks pass, 1 on failure, and 2 for an explicit skip, such as missing
native thClaws validation. An offline security pass alone does not establish `draft`.

## Deploy and tools

Set `SQLITE_READONLY_DB` in the daemon environment to an existing, readable SQLite
regular file, preferably using an absolute path. Missing, non-file, or unreadable
paths produce one stderr line and exit 1 before any protocol response. Launch
`python3 packs/sqlite-readonly/scripts/server.py` for direct MCP use.

The generated package bundles the script and `.thclaws/mcp.json` command/args,
without environment values. Start a dedicated thClaws daemon with its CWD set to
the generated package and supply the variable externally.

| Tool | Arguments | Result |
|---|---|---|
| `sqlite-readonly__tables` | `{}` | Tables/views sorted by name, excluding internal `sqlite_` names; table row counts, null for views |
| `sqlite-readonly__schema` | `{"table": "invoices"}` | Table name and columns: name, type, notnull, pk, default; unknown tables fail |
| `sqlite-readonly__query` | `{"sql": "SELECT * FROM invoices", "max_rows": 200}` | columns, rows, row_count (returned rows), truncated |

`max_rows` is optional, defaults to 200, and accepts integers from 1 to 1000.
SQL must be a non-empty string of at most 20000 characters. Extra arguments and
invalid types fail at the server with `isError: true`. Blob values become
`"<blob N bytes>"`; other SQLite values retain their JSON scalar forms.

## Read-only boundary

The connection uses a resolved, URI-quoted path with SQLite `mode=ro`. Its
authorizer denies everything except SELECT, READ, FUNCTION, RECURSIVE and these
schema PRAGMAs: `table_info`, `table_xinfo`, `index_list`, `index_info`,
`foreign_key_list`. Mutation, attachment and administrative actions are denied
by SQLite, including mutations hidden behind WITH.

The query gate strips leading whitespace/comments and one trailing semicolon,
requires a complete SELECT/WITH statement, and rejects any remaining semicolon
(including semicolons inside literals). It fetches at most `max_rows + 1` rows to
detect truncation. A progress handler checked every 10000 SQLite VM instructions
aborts after five seconds of wall time; SQLite errors, including interruption
and authorization failures, become `isError: true`. The same time bound applies
to table counts and schema inspection. This is not a hard OS execution deadline.

The file itself remains readable by the daemon user. There is no row-level
access control, data redaction, or isolation from other tools/processes with that
user's permissions. These mechanisms constrain this MCP connection; package
permission claims depend on its generated guarantee matrix and audit status.
Static audit can establish `draft`; live agent/tool evidence is separate.
