"""Probe the machine's timezone database; no fixture data or environment is needed."""
import json
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    ZoneInfo('UTC')
except (ZoneInfoNotFoundError, ValueError):
    print('datetime-tools fixture requires a timezone database containing UTC', file=sys.stderr)
    raise SystemExit(2) from None

print(json.dumps({}))
