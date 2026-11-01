"""Independent stdlib SQLite MCP: engine-enforced read-only access over stdio JSON-RPC."""
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import quote

TOOLS = [
    {'name': 'tables', 'description': 'List tables with row counts and views without counts.',
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'properties': {}},
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'schema', 'description': 'Read the columns of a table or view.',
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['table'],
                     'properties': {'table': {'type': 'string'}}},
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'query', 'description': 'Run one SELECT/WITH statement, returning at most max_rows rows.',
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['sql'],
                     'properties': {'sql': {'type': 'string', 'minLength': 1, 'maxLength': 20000},
                                    'max_rows': {'type': 'integer', 'minimum': 1, 'maximum': 1000,
                                                 'default': 200}}},
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
]


def authorize(action, arg1, arg2, database, source):
    if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                  sqlite3.SQLITE_RECURSIVE}:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and arg1 in {
        'table_info', 'table_xinfo', 'index_list', 'index_info', 'foreign_key_list',
    }:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def call(name, arguments):
    """Validate every argument at the server; SQLite remains the authority on allowed operations."""
    if not isinstance(name, str) or name not in {tool['name'] for tool in TOOLS}:
        raise ValueError('unknown tool')
    if not isinstance(arguments, dict):
        raise ValueError('arguments must be an object')  # noqa: TRY004 - MCP validation uses ValueError.
    deadline = time.monotonic() + 5
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
    try:
        if name == 'tables':
            if arguments:
                raise ValueError('tables expects no arguments')
            tables = []
            for table, kind in conn.execute(
                "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') "
                "AND name NOT GLOB 'sqlite_*' ORDER BY name"
            ).fetchall():
                quoted = '"' + table.replace('"', '""') + '"'
                rows = conn.execute(f'SELECT COUNT(*) FROM {quoted}').fetchone()[0] if kind == 'table' else None
                tables.append({'name': table, 'type': kind, 'rows': rows})
            return {'tables': tables}
        if name == 'schema':
            if set(arguments) != {'table'} or not isinstance(arguments['table'], str):
                raise ValueError('expected exactly one string argument: table')
            columns = conn.execute(
                'SELECT name, type, "notnull", pk, dflt_value FROM pragma_table_info(?)',
                (arguments['table'],),
            ).fetchall()
            if not columns:
                raise ValueError('unknown table')
            return {'table': arguments['table'], 'columns': [
                {'name': column, 'type': kind, 'notnull': bool(notnull), 'pk': pk, 'default': default}
                for column, kind, notnull, pk, default in columns
            ]}
        if 'sql' not in arguments or set(arguments) - {'sql', 'max_rows'}:
            raise ValueError('expected sql and optional max_rows')
        sql = arguments['sql']
        max_rows = arguments.get('max_rows', 200)
        if not isinstance(sql, str) or not sql.strip() or len(sql) > 20000:
            raise ValueError('sql must be a non-empty string of at most 20000 characters')
        if type(max_rows) is not int or not 1 <= max_rows <= 1000:
            raise ValueError('max_rows must be an integer from 1 to 1000')
        sql = re.sub(r'\A(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*', '', sql, flags=re.DOTALL)
        sql = sql.rstrip().removesuffix(';').rstrip()
        if ';' in sql or not sqlite3.complete_statement(sql + ';'):
            raise ValueError('expected one complete SQL statement')
        keyword = re.match(r'[A-Za-z]+', sql)
        if keyword is None or keyword.group().upper() not in {'SELECT', 'WITH'}:
            raise ValueError('only SELECT/WITH statements are allowed')
        cursor = conn.execute(sql)
        try:
            columns = [column[0] for column in cursor.description]
            rows = cursor.fetchmany(max_rows + 1)
        finally:
            cursor.close()
        return {'columns': columns, 'rows': [
            [f'<blob {len(value)} bytes>' if isinstance(value, bytes) else value for value in row]
            for row in rows[:max_rows]
        ], 'row_count': min(len(rows), max_rows), 'truncated': len(rows) > max_rows}
    except sqlite3.DatabaseError as exc:
        raise ValueError(str(exc)) from None
    finally:
        conn.set_progress_handler(None, 0)


def handle(request):
    method = request.get('method')
    if method == 'initialize':
        return {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                'serverInfo': {'name': 'sqlite-readonly', 'version': '0.1.0'}}
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': TOOLS}
    if method == 'tools/call':
        params = request.get('params', {})
        try:
            if not isinstance(params, dict):
                raise ValueError('params must be an object')  # noqa: TRY004 - MCP validation uses ValueError.
            result = call(params.get('name'), params.get('arguments', {}))
        except ValueError as exc:
            return {'isError': True, 'content': [{'type': 'text', 'text': str(exc)}]}
        return {'isError': False, 'content': [{'type': 'text', 'text': json.dumps(result, sort_keys=True)}]}
    raise KeyError(method)


try:
    configured = os.environ.get('SQLITE_READONLY_DB')
    if not configured:
        raise ValueError('SQLITE_READONLY_DB is required')
    path = Path(configured).resolve()
    if not path.is_file() or not os.access(path, os.R_OK):
        raise ValueError('SQLITE_READONLY_DB must be an existing readable regular file')
    conn = sqlite3.connect(f'file:{quote(str(path))}?mode=ro', uri=True, check_same_thread=False)
    conn.set_authorizer(authorize)
except (ValueError, OSError, sqlite3.DatabaseError) as exc:
    print(' '.join(str(exc).splitlines()), file=sys.stderr, flush=True)
    raise SystemExit(1) from None

for line in sys.stdin:
    try:
        request = json.loads(line)
        if not isinstance(request, dict) or request.get('jsonrpc') != '2.0':
            raise ValueError('invalid request')
    except ValueError:
        print(json.dumps({'jsonrpc': '2.0', 'id': None,
                          'error': {'code': -32600, 'message': 'invalid request'}}), flush=True)
        continue
    if 'id' not in request:
        continue  # notifications such as notifications/initialized need no reply
    try:
        response = {'jsonrpc': '2.0', 'id': request['id'], 'result': handle(request)}
    except KeyError:
        response = {'jsonrpc': '2.0', 'id': request['id'],
                    'error': {'code': -32601, 'message': 'method not found'}}
    print(json.dumps(response), flush=True)
