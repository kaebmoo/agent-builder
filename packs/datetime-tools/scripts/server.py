"""Independent stdlib date/time MCP: no configured env, data files or network."""
import json
import re
import sys
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
UNITS = {'seconds': 1, 'minutes': 60, 'hours': 3600, 'days': 86400}
TOOLS = [
    {'name': 'now', 'description': 'Read the current time in an IANA timezone (default UTC).',
     'inputSchema': {'type': 'object', 'additionalProperties': False,
                     'properties': {'timezone': {'type': 'string', 'default': 'UTC'}}},
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'convert', 'description': 'Convert an ISO datetime; an explicit input offset overrides from_timezone.',
     'inputSchema': {'type': 'object', 'additionalProperties': False,
                     'required': ['datetime', 'from_timezone', 'to_timezone'],
                     'properties': {'datetime': {'type': 'string'}, 'from_timezone': {'type': 'string'},
                                    'to_timezone': {'type': 'string'}}},
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'diff', 'description': 'Compute end minus start; naive ISO datetimes are UTC.',
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['start', 'end', 'unit'],
                     'properties': {'start': {'type': 'string'}, 'end': {'type': 'string'},
                                    'unit': {'enum': list(UNITS)}}},
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'add_business_days', 'description': 'Move by business days, skipping weekdays in weekend; no holidays.',
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['date', 'days'],
                     'properties': {'date': {'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}$'},
                                    'days': {'type': 'integer', 'minimum': -3650, 'maximum': 3650},
                                    'weekend': {'type': 'array', 'minItems': 1, 'maxItems': 6,
                                                'uniqueItems': True, 'items': {'enum': WEEKDAYS},
                                                'default': ['saturday', 'sunday']}}},
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
]


def zone(name):
    if not isinstance(name, str):
        raise ValueError('timezone must be a string')  # noqa: TRY004 - MCP validation uses ValueError.
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f'unknown timezone: {name}') from None


def parse_datetime(value):
    if not isinstance(value, str):
        raise ValueError('datetime must be an ISO 8601 string')  # noqa: TRY004 - MCP validation uses ValueError.
    try:
        return datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
    except ValueError:
        raise ValueError(f'invalid datetime: {value}') from None


def describe(value, name):
    return {'iso': value.isoformat(), 'timezone': name, 'weekday': WEEKDAYS[value.weekday()]}


def human_duration(delta):
    remaining = abs(delta // timedelta(microseconds=1))
    parts = []
    for label, size in [('day', 86400), ('hour', 3600), ('minute', 60)]:
        count, remaining = divmod(remaining, size * 1000000)
        if count:
            parts.append(f'{count} {label}' + ('s' if count != 1 else ''))
    if remaining:
        seconds = f'{remaining / 1000000:.6f}'.rstrip('0').rstrip('.')
        parts.append(f'{seconds} second' + ('s' if remaining != 1000000 else ''))
    if not parts:
        return '0 seconds'
    return ('-' if delta < timedelta(0) else '') + ' '.join(parts)


def call(name, arguments):
    if not isinstance(name, str) or name not in {tool['name'] for tool in TOOLS}:
        raise ValueError('unknown tool')
    if not isinstance(arguments, dict):
        raise ValueError('arguments must be an object')  # noqa: TRY004 - MCP validation uses ValueError.
    required, optional = {
        'now': (set(), {'timezone'}),
        'convert': ({'datetime', 'from_timezone', 'to_timezone'}, set()),
        'diff': ({'start', 'end', 'unit'}, set()),
        'add_business_days': ({'date', 'days'}, {'weekend'}),
    }[name]
    if not required <= set(arguments) or set(arguments) - required - optional:
        raise ValueError(f'{name}: invalid argument set')
    try:
        if name == 'now':
            name = arguments.get('timezone', 'UTC')
            value = datetime.now(zone(name)).replace(microsecond=0)
            return {**describe(value, name), 'unix': int(value.timestamp())}
        if name == 'convert':
            source = zone(arguments['from_timezone'])
            target = zone(arguments['to_timezone'])
            value = parse_datetime(arguments['datetime'])
            if value.tzinfo is None:
                value = value.replace(tzinfo=source)
            return describe(value.astimezone(target), arguments['to_timezone'])
        if name == 'diff':
            unit = arguments['unit']
            if not isinstance(unit, str) or unit not in UNITS:
                raise ValueError('unknown unit')
            start, end = [parse_datetime(arguments[key]) for key in ('start', 'end')]
            start = start.replace(tzinfo=UTC) if start.tzinfo is None else start
            end = end.replace(tzinfo=UTC) if end.tzinfo is None else end
            delta = end.astimezone(UTC) - start.astimezone(UTC)
            return {'value': round(delta.total_seconds() / UNITS[unit], 6),
                    'unit': unit, 'human': human_duration(delta)}
        raw_date = arguments['date']
        if not isinstance(raw_date, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', raw_date):
            raise ValueError('date must be a YYYY-MM-DD string')
        value = date.fromisoformat(raw_date)
        days = arguments['days']
        if type(days) is not int or not -3650 <= days <= 3650:
            raise ValueError('days must be an integer from -3650 to 3650')
        weekend = arguments.get('weekend', ['saturday', 'sunday'])
        if (not isinstance(weekend, list) or not 1 <= len(weekend) <= 6
                or any(not isinstance(day, str) or day not in WEEKDAYS for day in weekend)
                or len(set(weekend)) != len(weekend)):
            raise ValueError('weekend must contain 1 to 6 unique lowercase weekday names')
        step = timedelta(days=1 if days >= 0 else -1)
        remaining = abs(days)
        while remaining:
            value += step
            if WEEKDAYS[value.weekday()] not in weekend:
                remaining -= 1
        return {'date': value.isoformat(), 'weekday': WEEKDAYS[value.weekday()]}
    except OverflowError:
        raise ValueError('date or datetime exceeds the supported range') from None


def handle(request):
    method = request.get('method')
    if method == 'initialize':
        return {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                'serverInfo': {'name': 'datetime-tools', 'version': '0.1.0'}}
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
    ZoneInfo('UTC')
except (ZoneInfoNotFoundError, ValueError):
    print('datetime-tools requires a timezone database containing UTC', file=sys.stderr, flush=True)
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
