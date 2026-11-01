# Datetime tools pack

An independent stdio MCP server for date/time questions, using Python 3.11+ and
the standard library only. Any agent with MCP support can use it without a
provider key, network, configured environment variables or input files.
Conversion, duration and business-day calculations are deterministic for the
same inputs and timezone database; `now` reads the machine's clock.

## Run

From the agent-builder checkout:

```bash
forge pack test datetime-tools
forge generate fixtures/datetime-helper/spec.yaml --out /tmp/datetime-helper
forge audit /tmp/datetime-helper --security
```

Fixture setup probes `ZoneInfo("UTC")`, prints `{}`, and creates no data. The
harness supplies only PATH. Pack test exit codes: 0 = conformant (all 13 cases);
1 = descriptor, protocol or case failure; 2 = missing runtime (`python3` absent
from PATH or fixture setup cannot load UTC from the timezone database).
The harness writes ignored local `pack-conformance.json` evidence containing
digests and verdicts. Changed assets require a new test. Generate returns 0 on
success and 1 on failure; audit returns 0 when checks pass, 1 on failure, and 2
for an explicit skip such as missing native thClaws validation. An offline
security pass alone does not establish `draft`.

## Deploy and tools

No environment variables are required. Launch
`python3 packs/datetime-tools/scripts/server.py` for direct MCP use. If UTC
cannot load at startup, the server prints one stderr line and exits 1 before
replying. The generated standalone example bundles the script and
`.thclaws/mcp.json` command/args; no external configuration is needed.

| Tool | Arguments | Result / example |
|---|---|---|
| `datetime-tools__now` | `{"timezone": "Asia/Bangkok"}` (`timezone` optional, default `UTC`) | `iso` with offset at seconds precision, `timezone`, lowercase `weekday`, integer Unix `unix` |
| `datetime-tools__convert` | `{"datetime": "2026-01-01T09:00:00", "from_timezone": "Asia/Bangkok", "to_timezone": "UTC"}` | `{"iso": "2026-01-01T02:00:00+00:00", "timezone": "UTC", "weekday": "thursday"}` |
| `datetime-tools__diff` | `{"start": "2026-01-01T00:00:00", "end": "2026-01-02T06:00:00", "unit": "hours"}` | `{"value": 30.0, "unit": "hours", "human": "1 day 6 hours"}` |
| `datetime-tools__add_business_days` | `{"date": "2026-10-30", "days": 2}` (optional `weekend: ["saturday", "sunday"]`) | `{"date": "2026-11-03", "weekday": "tuesday"}` |

Timezones are IANA names resolved by `zoneinfo.ZoneInfo`. Unknown names return
`isError: true` with `unknown timezone: <name>`. Datetimes use
`datetime.fromisoformat`, including a trailing `Z` as `+00:00`. Conversion
interprets naive inputs in `from_timezone`; an explicit input offset takes
precedence, but both timezone arguments must still be valid. Microseconds are
preserved by conversion. Ambiguous or nonexistent naive local times follow
ZoneInfo's default `fold=0` behavior; the tool does not reject DST gaps.

Diff treats naive inputs as UTC and computes end minus start in `seconds`,
`minutes`, `hours` or `days`, rounded to six decimal places. A day is 24 hours.
The human duration omits zero parts, preserves fractional seconds up to six
decimal places, prefixes negative durations with `-`, and uses `0 seconds` for
zero.

Business-day arithmetic accepts `YYYY-MM-DD` dates parsed by `date.fromisoformat`
and integer `days` from -3650 to 3650 (booleans are rejected). It excludes the
starting date when counting, moves backward for negative days, and leaves the
input unchanged for zero days. `weekend` must contain 1–6 unique lowercase
English weekday names (`monday` through `sunday`); it defaults to Saturday and
Sunday. For example, `2026-11-02` minus one business day with
`weekend: ["friday", "saturday"]` returns `2026-11-01` (`sunday`). Unknown
arguments, missing required arguments, invalid types, malformed values and date
overflow return `isError: true`.

## Read-only boundary

The tools perform no data-file reads or writes, shell commands or network
requests, and read no environment configuration. Python's ZoneInfo loads the
machine's timezone database, so conversion results depend on its installed
version and available zones. `now` depends on the machine's clock. There are no
holiday calendars and no natural-language parsing. Supported dates are within
Python's years 1–9999; arithmetic outside that range fails.

These properties describe this MCP implementation. Package permission claims
depend on the generated guarantee matrix and audit status; static audit can
establish `draft`, while live agent/tool evidence is separate.
