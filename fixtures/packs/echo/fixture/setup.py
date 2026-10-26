"""Echo needs no data and no env: print an empty JSON object, which equals the declared env list."""
import json
import sys
from pathlib import Path

Path(sys.argv[1]).resolve()  # the harness owns and removes this directory
print(json.dumps({}))
