"""Synthetic preview fixture: no credentials, external services or real delivery."""
import json
import sys
from pathlib import Path

work = Path(sys.argv[1]).resolve()
for name in ('input', 'state'):
    (work / name).mkdir()
(work / 'input/report.txt').write_text('Synthetic publication fixture.\n')
# Dry run checks provisioning only; these empty files are never used for a connection.
for name in ('identity', 'known_hosts'):
    (work / name).touch()
config = {
    'smtp': {'host': 'smtp.example.invalid', 'port': 465,
             'sender': 'sender@example.invalid', 'recipient': 'recipient@example.invalid'},
    'sftp': {'host': 'sftp.example.invalid', 'port': 22, 'username': 'fixture',
             'identity_file': str(work / 'identity'), 'known_hosts': str(work / 'known_hosts'),
             'remote_dir': '/publications'},
}
print(json.dumps({'PUBLISHER_CONFIG': json.dumps(config), 'PUBLISHER_INPUT_ROOT': str(work / 'input'),
                  'PUBLISHER_STATE_DIR': str(work / 'state')}))
