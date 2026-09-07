---
name: sql-readonly
description: Query the explicitly configured SQLite source through the SQL pack.
---
Inspect `sql-readonly__metadata`, validate a SELECT with `sql-readonly__validate`,
then call `sql-readonly__query`. Return statement, columns and rows as JSON.
Never use Bash, a different database, admin tools or a validation bypass.
Reject mutation requests using the agent's refusal contract.
The SQL server refuses mutations; this does not make the whole /agent/run runtime read-only.
