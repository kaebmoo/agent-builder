"""Independent stdlib CSV/TSV MCP: read-only files confined to one configured directory."""
import csv
import json
import os
import sys
from pathlib import Path, PureWindowsPath

TOOLS = [
    {'name': 'files', 'description': 'List CSV/TSV files recursively, skipping symlinks and hidden directories.',
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'properties': {}},
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'columns', 'description': 'Read the header, delimiter and data row count.',
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['file'],
                     'properties': {'file': {'type': 'string', 'minLength': 1}}},
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'preview', 'description': 'Preview up to 50 data rows as strings.',
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['file'],
                     'properties': {'file': {'type': 'string', 'minLength': 1},
                                    'rows': {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 5}}},
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'aggregate', 'description': 'Count or aggregate numeric values, optionally filtered and grouped.',
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['file', 'metric'],
                     'properties': {'file': {'type': 'string', 'minLength': 1},
                                    'metric': {'enum': ['count', 'sum', 'avg', 'min', 'max']},
                                    'column': {'type': 'string'}, 'group_by': {'type': 'string'},
                                    'where': {'type': 'object', 'additionalProperties': False,
                                              'required': ['column', 'equals'],
                                              'properties': {'column': {'type': 'string'},
                                                             'equals': {'type': 'string'}}}}},
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
]


def locate(file):
    message = 'file must be a .csv or .tsv path inside CSV_READONLY_ROOT'
    try:
        if (not isinstance(file, str) or not file or Path(file).is_absolute()
                or PureWindowsPath(file).drive or '..' in Path(file).parts
                or '..' in PureWindowsPath(file).parts):
            raise ValueError(message)
        candidate = ROOT / file
        # Check the original components: resolve() would erase symlinks pointing inside ROOT.
        if any(part.is_symlink() for part in [candidate, *candidate.parents] if part != ROOT
               and part.is_relative_to(ROOT)):
            raise ValueError(message)
        target = candidate.resolve()
        if (not target.is_relative_to(ROOT) or not target.is_file()
                or target.suffix not in {'.csv', '.tsv'}):
            raise ValueError(message)
        return target
    except (ValueError, OSError, RuntimeError):
        raise ValueError(message) from None


def read_data(file):
    target = locate(file)
    with open(target, 'r', encoding='utf-8-sig', newline='') as source:
        delimiter = '\t'
        if target.suffix == '.csv':
            sample = source.read(4096)
            source.seek(0)
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=',;|\t').delimiter
            except csv.Error:
                delimiter = ','
        reader = csv.DictReader(source, delimiter=delimiter)
        columns = reader.fieldnames
        if columns is None:
            raise ValueError('file has no header')
        return target.relative_to(ROOT).as_posix(), delimiter, columns, list(reader)


def aggregate(file, columns, rows, arguments):
    metric = arguments['metric']
    column = arguments.get('column')
    group_by = arguments.get('group_by')
    where = arguments.get('where')
    for name in [column, group_by, where['column'] if where is not None else None]:
        if name is not None and name not in columns:
            raise ValueError(f'unknown column: {name}')
    if metric != 'count' and column is None:
        raise ValueError('column is required for numeric metrics')
    groups = {} if group_by is not None else {None: []}
    for number, row in enumerate(rows, start=2):
        if where is not None and row.get(where['column']) != where['equals']:
            continue
        key = str(row.get(group_by)) if group_by is not None else None
        value = 1
        if metric != 'count':
            raw = row.get(column)
            try:
                value = float(raw.strip().replace(',', ''))
                if not -float('inf') < value < float('inf'):
                    raise ValueError
            except (ValueError, AttributeError):
                raise ValueError(f'non-numeric value in column {column} at row {number}') from None
        groups.setdefault(key, []).append(value)
    result = []
    for key in sorted(groups, key=lambda key: '' if key is None else key)[:200]:
        values = groups[key]
        if metric == 'count':
            value = len(values)
        elif not values:
            value = 0
        elif metric == 'min':
            value = min(values)
        elif metric == 'max':
            value = max(values)
        else:
            value = sum(values) / (len(values) if metric == 'avg' else 1)
        if not -float('inf') < value < float('inf'):
            raise ValueError('aggregate exceeds the finite numeric range')
        result.append({'key': key, 'value': value})
    return {'file': file, 'metric': metric, 'column': None if metric == 'count' else column,
            'group_by': group_by, 'groups': result, 'truncated': len(groups) > 200}


def call(name, arguments):
    if not isinstance(name, str) or name not in {tool['name'] for tool in TOOLS}:
        raise ValueError('unknown tool')
    if not isinstance(arguments, dict):
        raise ValueError('arguments must be an object')  # noqa: TRY004 - MCP validation uses ValueError.
    required, optional = {
        'files': (set(), set()), 'columns': ({'file'}, set()),
        'preview': ({'file'}, {'rows'}),
        'aggregate': ({'file', 'metric'}, {'column', 'group_by', 'where'}),
    }[name]
    if not required <= set(arguments) or set(arguments) - required - optional:
        raise ValueError(f'{name}: invalid argument set')
    if name != 'files' and not isinstance(arguments['file'], str):
        raise ValueError('file must be a string')
    if name == 'preview':
        count = arguments.get('rows', 5)
        if type(count) is not int or not 1 <= count <= 50:
            raise ValueError('rows must be an integer from 1 to 50')
    if name == 'aggregate':
        metric = arguments['metric']
        if not isinstance(metric, str) or metric not in {'count', 'sum', 'avg', 'min', 'max'}:
            raise ValueError('unknown metric')
        for key in ('column', 'group_by'):
            if key in arguments and not isinstance(arguments[key], str):
                raise ValueError(f'{key} must be a string')
        if 'where' in arguments:
            where = arguments['where']
            if (not isinstance(where, dict) or set(where) != {'column', 'equals'}
                    or any(not isinstance(value, str) for value in where.values())):
                raise ValueError('where requires exactly column and equals strings')
    try:
        if name == 'files':
            files = []
            for directory, dirs, names in os.walk(ROOT, followlinks=False):
                dirs[:] = [d for d in dirs if not d.startswith('.') and not (Path(directory) / d).is_symlink()]
                for filename in names:
                    path = Path(directory) / filename
                    if path.is_symlink() or not path.is_file() or path.suffix not in {'.csv', '.tsv'}:
                        continue
                    relative = path.relative_to(ROOT).as_posix()
                    files.append({'path': relative, 'size_bytes': locate(relative).stat().st_size})
            return {'files': sorted(files, key=lambda entry: entry['path'])}
        file, delimiter, columns, rows = read_data(arguments['file'])
        if name == 'columns':
            return {'file': file, 'delimiter': delimiter, 'columns': columns, 'row_count': len(rows)}
        if name == 'preview':
            return {'file': file, 'columns': columns, 'rows': [
                {str(key): str(value) for key, value in row.items()} for row in rows[:count]
            ]}
        return aggregate(file, columns, rows, arguments)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValueError(str(exc)) from None


def handle(request):
    method = request.get('method')
    if method == 'initialize':
        return {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                'serverInfo': {'name': 'csv-readonly', 'version': '0.1.0'}}
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
    configured = os.environ.get('CSV_READONLY_ROOT')
    if not configured:
        raise ValueError('CSV_READONLY_ROOT is required')
    ROOT = Path(configured).resolve()
    if not ROOT.is_dir():
        raise ValueError('CSV_READONLY_ROOT must be an existing directory')
except (ValueError, OSError, RuntimeError) as exc:
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
