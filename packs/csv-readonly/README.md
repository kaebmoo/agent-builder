# CSV read-only pack

An independent stdio MCP server for inspecting CSV/TSV files under one configured
directory. It uses Python 3.11+ and the standard library only, without a provider
key, network, external repository or additional dependency. Any agent with MCP
support can use it.

## Run

From the agent-builder checkout:

```bash
forge pack test csv-readonly
forge generate fixtures/csv-reader/spec.yaml --out /tmp/csv-reader
forge audit /tmp/csv-reader --security
```

The fixture creates temporary synthetic data: eight sales rows in `data/sales.csv`,
four people in `data/people.tsv`, ignored `data/notes.txt`, and `outside.csv` outside
the configured root to test confinement. The harness removes the data afterward
and supplies only PATH and the declared environment variable.

Pack test exit codes: 0 = conformant (all 15 cases); 1 = descriptor, protocol or
case failure; 2 = missing runtime (`python3` absent from PATH). Fixture setup uses
only stdlib and never returns 2. The harness writes ignored local
`pack-conformance.json` evidence containing digests and verdicts, without environment
values or result rows. Changed assets require a new test. Generate returns 0 on
success and 1 on failure; audit returns 0 when checks pass, 1 on failure, and 2
for an explicit skip such as missing native thClaws validation. An offline security
pass alone does not establish `draft`.

## Deploy and tools

Set `CSV_READONLY_ROOT` in the daemon environment to an existing directory,
preferably using an absolute path. The server resolves it at startup; missing or
non-directory roots produce one stderr line and exit 1 before any protocol response.
Launch `python3 packs/csv-readonly/scripts/server.py` for direct MCP use.

The generated standalone package bundles the script and `.thclaws/mcp.json`
command/args, without environment values. Supply the variable externally when
running the generated workflow from the package directory.

| Tool | Arguments | Result |
|---|---|---|
| `csv-readonly__files` | `{}` | files with relative POSIX path and size_bytes, recursively sorted by path |
| `csv-readonly__columns` | `{"file": "sales.csv"}` | file, delimiter, columns, row_count excluding the header |
| `csv-readonly__preview` | `{"file": "sales.csv", "rows": 5}` | file, columns, rows as dictionaries of strings |
| `csv-readonly__aggregate` | `{"file": "sales.csv", "metric": "sum", "column": "amount", "group_by": "region"}` | file, metric, column, group_by, groups of key/value, truncated |

`rows` defaults to 5 and accepts integers from 1 to 50. Aggregate metrics are
`count`, `sum`, `avg`, `min`, `max`. Numeric metrics require `column`; count ignores
its values and returns `column: null`. All supplied column names must exist.
Optional `where: {"column": "product", "equals": "widget"}` matches exact strings
before aggregation. Numbers use `float()` after stripping spaces and commas;
invalid or non-finite values fail with the row number (header is row 1).
Numeric overflow also fails. Groups sort by key and return at most 200 entries,
with `truncated: true` when more exist. Without grouping there is exactly one
group with `key: null`; an empty selection returns value 0 for every metric.

Unknown arguments, invalid types, missing headers and unknown columns/metrics
fail at the server with `isError: true`. Preview preserves DictReader's extra-cell
list under the string key `"None"`, and missing-cell values as `"None"`; all values
are converted with `str()`.

## Read-only boundary

Every data file is opened only with `open(target, 'r', encoding='utf-8-sig', newline='')`.
File arguments must be non-empty relative paths without a drive or `..` component.
The resolved target must be a regular `.csv` or `.tsv` file inside the resolved
root. Symlinks in any component below the root are rejected, including links to
files inside the root. The file listing skips symlinks and directories beginning
with `.`; other extensions are ignored.

Whole files are read into memory per data call. There is no type inference beyond
float parsing, no streaming aggregation or execution deadline. TSV always uses a
tab; CSV uses Sniffer heuristics over the first 4 KiB with comma, semicolon, pipe
or tab candidates, falling back to comma. Heuristics can misidentify delimiters.
UTF-8 BOMs are accepted. Duplicate headers and uneven rows follow DictReader's
behavior, so duplicate column names can overwrite earlier cells.

Path checks and read mode constrain this MCP server, without OS filesystem
isolation against concurrent path replacement or other processes/tools. There is
no row-level access control or redaction. Package permission claims depend on
the generated guarantee matrix and audit status; static audit can establish
`draft`, while live agent/tool evidence is separate.
