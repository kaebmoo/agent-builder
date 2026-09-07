"""Emit synthetic fixture env as JSON; argv[1] is owned and removed by the harness."""
import shutil
import subprocess
import json
import sqlite3
import sys
from pathlib import Path

# A sibling checkout is a fixture convenience only; deployers provision this path in daemon env.
root = Path(__file__).resolve().parents[4] / 'AI'
runtime = shutil.which('python3')
if (not (root / 'mcp_servers/nt_query_mcp.py').is_file() or runtime is None
        or subprocess.run([runtime, '-c', 'import mcp'], capture_output=True, check=False).returncode):
    print('SQL MCP runtime requires the project AI sibling checkout and mcp Python dependency', file=sys.stderr)
    raise SystemExit(2)
path = Path(sys.argv[1]).resolve() / 'invoices.db'
with sqlite3.connect(path) as connection:
    connection.execute('CREATE TABLE invoices (vendor TEXT, total INTEGER, invoice_date TEXT)')
    connection.executemany('INSERT INTO invoices VALUES (?, ?, ?)',
                           [('Example A', 100, '2026-08-01'), ('Example A', 200, '2026-08-15'),
                            ('Example B', 50, '2026-08-20')])
print(json.dumps({'SQL_READONLY_DSN': 'sqlite:///' + str(path), 'SQL_READONLY_AI_ROOT': str(root)}))
