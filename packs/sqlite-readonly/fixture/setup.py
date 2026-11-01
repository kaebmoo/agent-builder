"""Create deterministic synthetic SQLite data in the harness-owned temporary directory."""
import json
import sys
from pathlib import Path

try:
    import sqlite3
except ImportError:
    print('SQLite runtime unavailable', file=sys.stderr)
    raise SystemExit(2) from None

path = Path(sys.argv[1]).resolve() / 'demo.sqlite'
path.unlink(missing_ok=True)
with sqlite3.connect(path) as conn:
    conn.execute('CREATE TABLE vendors(id INTEGER PRIMARY KEY, name TEXT)')
    conn.execute('CREATE TABLE invoices(id INTEGER PRIMARY KEY, vendor TEXT, total REAL, issued_on TEXT)')
    conn.executemany('INSERT INTO vendors VALUES (?, ?)', [(1, 'Acme'), (2, 'Beacon'), (3, 'Cedar')])
    conn.executemany('INSERT INTO invoices VALUES (?, ?, ?, ?)', [
        (1, 'Acme', 120.0, '2026-08-01'),
        (2, 'Beacon', 75.5, '2026-08-04'),
        (3, 'Cedar', 200.0, '2026-08-09'),
        (4, 'Acme', 80.0, '2026-08-15'),
        (5, 'Beacon', 24.5, '2026-08-20'),
        (6, 'Cedar', 50.0, '2026-08-31'),
    ])
conn.close()
print(json.dumps({'SQLITE_READONLY_DB': str(path)}))
