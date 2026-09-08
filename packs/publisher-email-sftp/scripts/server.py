"""T2 publisher: fixed operator destinations, explicit dry runs, persistent attempt deduplication."""
from __future__ import annotations

import hashlib
import json
import os
import re
import smtplib
import sqlite3
import ssl
import stat
import subprocess
import sys
import tempfile
from contextlib import closing
from email.message import EmailMessage
from pathlib import Path

LIMIT = 10 * 1024 * 1024
COMMON = {
    'dry_run': {'type': 'boolean'},
    'idempotency_key': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
}
PROPERTIES = {
    'send_email': {**COMMON, 'subject': {'type': 'string', 'minLength': 1, 'maxLength': 200},
                   'body': {'type': 'string', 'minLength': 1, 'maxLength': 1000000}},
    'upload_file': {**COMMON, 'filename': {'type': 'string', 'pattern': '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$'}},
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def required_text(value, label, pattern=None, maximum=4096):
    if (not isinstance(value, str) or not value or len(value) > maximum
            or (pattern and not re.fullmatch(pattern, value))):
        raise ValueError(f'invalid {label}')
    return value


def directory(variable):
    root = Path(os.environ[variable])
    if not root.is_absolute() or not root.is_dir():
        raise ValueError(f'{variable} must be an existing absolute directory')
    return root.resolve()


def prepare(tool, args):
    if tool not in PROPERTIES or not isinstance(args, dict) or set(args) != set(PROPERTIES[tool]):
        raise ValueError('unknown tool or missing/unexpected arguments')
    if type(args['dry_run']) is not bool:
        raise ValueError('dry_run must be an explicit boolean')
    required_text(args['idempotency_key'], 'idempotency_key', r'[A-Za-z0-9_-]{1,128}')
    config = json.loads(os.environ['PUBLISHER_CONFIG'])
    channel = 'smtp' if tool == 'send_email' else 'sftp'
    settings = config[channel]
    required_text(settings['host'], 'host', r'[A-Za-z0-9][A-Za-z0-9.-]*')
    if type(settings['port']) is not int or not 1 <= settings['port'] <= 65535:
        raise ValueError('invalid port')
    data = None
    if tool == 'send_email':
        required_text(args['subject'], 'subject', r'[^\r\n]+', maximum=200)
        required_text(args['body'], 'body', maximum=1000000)
        for field in ('sender', 'recipient'):
            required_text(settings[field], field, r'[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+')
        if 'username' in settings or 'password' in settings:
            required_text(settings['username'], 'username')
            required_text(settings['password'], 'password')
        destination = {k: settings[k] for k in ('host', 'port', 'sender', 'recipient')}
    else:
        filename = required_text(args['filename'], 'filename', r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')
        required_text(settings['username'], 'username', r'[A-Za-z0-9_][A-Za-z0-9_-]*')
        remote = required_text(settings['remote_dir'], 'remote_dir', r'/[A-Za-z0-9_./-]*')
        if any(part in ('.', '..') for part in remote.split('/')):
            raise ValueError('invalid remote_dir')
        for field in ('identity_file', 'known_hosts'):
            if not Path(settings[field]).is_absolute() or not Path(settings[field]).is_file():
                raise ValueError(f'{field} must be a provisioned absolute file')
        # One basename beneath an operator-owned directory; never follow a symlink or open a FIFO.
        root_fd = os.open(directory('PUBLISHER_INPUT_ROOT'), os.O_RDONLY)
        try:
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
        finally:
            os.close(root_fd)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('artifact must be a regular file')
            data = stream.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError('artifact exceeds 10 MiB')
        destination = {k: settings[k] for k in ('host', 'port', 'username', 'remote_dir')}
    payload = {'tool': tool, 'arguments': {k: v for k, v in args.items() if k not in COMMON},
               'destination': destination, 'artifact_sha256': sha(data) if data is not None else None}
    return settings, data, sha(canonical(payload))


def deliver(tool, args, settings, data):
    if tool == 'send_email':
        message = EmailMessage()
        message['From'], message['To'], message['Subject'] = settings['sender'], settings['recipient'], args['subject']
        message.set_content(args['body'])
        with smtplib.SMTP_SSL(settings['host'], settings['port'], timeout=30,
                              context=ssl.create_default_context()) as smtp:
            if 'username' in settings:
                smtp.login(settings['username'], settings['password'])
            if smtp.send_message(message, from_addr=settings['sender'], to_addrs=[settings['recipient']]):
                raise ValueError('recipient refused')
    else:
        # Native OpenSSH, no shell, no user ssh config, no host-key auto-accept or interactive fallback.
        argv = ['sftp', '-F', '/dev/null', '-b', '-', '-P', str(settings['port']),
                '-i', settings['identity_file'], '-oBatchMode=yes', '-oIdentitiesOnly=yes',
                '-oIdentityAgent=none', '-oStrictHostKeyChecking=yes',
                '-oUserKnownHostsFile=' + settings['known_hosts'], '-oGlobalKnownHostsFile=/dev/null',
                '-oConnectTimeout=30', '-oProxyCommand=none', '-oPermitLocalCommand=no',
                settings['username'] + '@' + settings['host']]
        with tempfile.TemporaryDirectory(prefix='publisher-upload-') as temporary:
            Path(temporary, 'artifact').write_bytes(data)
            target = settings['remote_dir'].rstrip('/') + '/' + args['filename']
            subprocess.run(argv, input=f'put artifact {target}\n', text=True, cwd=temporary,
                           env={'PATH': os.environ.get('PATH', os.defpath)}, capture_output=True,
                           timeout=60, check=True)


def publish(tool, args):
    settings, data, fingerprint = prepare(tool, args)
    if args['dry_run']:
        return {'status': 'dry_run', 'request_sha256': fingerprint}
    ledger = directory('PUBLISHER_STATE_DIR') / 'publisher.db'
    if ledger.is_symlink():
        raise ValueError('ledger must not be a symlink')
    key = sha(args['idempotency_key'].encode())
    # Reserve durably BEFORE any send. A crash or uncertain transport outcome stays pending;
    # retrying it must never send again without operator reconciliation.
    with closing(sqlite3.connect(ledger, timeout=10)) as db:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS attempts (key TEXT PRIMARY KEY, fingerprint TEXT, status TEXT)')
        with db:
            inserted = db.execute('INSERT OR IGNORE INTO attempts VALUES (?, ?, ?)',
                                  (key, fingerprint, 'pending')).rowcount
            previous = db.execute('SELECT fingerprint, status FROM attempts WHERE key = ?', (key,)).fetchone()
        if previous[0] != fingerprint:
            raise ValueError('idempotency_key conflicts with a different payload or destination')
        if not inserted:
            if previous[1] != 'sent':
                raise ValueError('delivery outcome pending/unknown; operator reconciliation required')
            return {'status': 'sent', 'request_sha256': fingerprint}
        try:
            deliver(tool, args, settings, data)
        except (OSError, ValueError, subprocess.SubprocessError):
            # Never return SMTP credentials, subprocess output or server response text to the model.
            raise ValueError('delivery outcome unknown; operator reconciliation required') from None
        with db:
            db.execute("UPDATE attempts SET status = 'sent' WHERE key = ?", (key,))
    return {'status': 'sent', 'request_sha256': fingerprint}


def tool_call(params):
    try:
        result = publish(params['name'], params.get('arguments', {}))
    except ValueError as exc:
        # Only our own validation messages; JSON/config parsing errors may contain configuration fragments.
        message = 'invalid publisher configuration' if isinstance(exc, json.JSONDecodeError) else str(exc)
        return {'isError': True, 'content': [{'type': 'text', 'text': message}]}
    except (KeyError, TypeError, OSError, sqlite3.Error):
        return {'isError': True, 'content': [{'type': 'text', 'text': 'invalid publisher configuration or artifact'}]}
    return {'isError': False, 'content': [{'type': 'text', 'text': json.dumps(result, sort_keys=True)}]}


def main():
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or request.get('jsonrpc') != '2.0':
                raise ValueError('invalid request')
            if 'id' not in request:
                continue
            method = request.get('method')
            if method == 'initialize':
                result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'publisher-email-sftp', 'version': '0.1.0'}}
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = {'tools': [{'name': name, 'description': 'Publish to an operator-configured destination. '
                                     'dry_run=true previews without sending; retries require the same key and payload.',
                                     'inputSchema': {'type': 'object', 'additionalProperties': False,
                                                     'properties': props, 'required': list(props)}}
                                    for name, props in PROPERTIES.items()]}
            elif method == 'tools/call':
                result = tool_call(request.get('params', {}))
            else:
                print(json.dumps({'jsonrpc': '2.0', 'id': request['id'],
                                  'error': {'code': -32601, 'message': 'method not found'}}), flush=True)
                continue
            response = {'jsonrpc': '2.0', 'id': request['id'], 'result': result}
        except (ValueError, TypeError):
            response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'invalid request'}}
        print(json.dumps(response), flush=True)


if __name__ == '__main__':
    main()
