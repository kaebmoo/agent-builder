"""Reference stdio MCP for the pack contract (DESIGN §5): stdlib only, no env, no network, no side effects."""
import json
import sys

TOOLS = [
    {'name': 'echo', 'description': 'Return the supplied text unchanged.',
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['text'],
                     'properties': {'text': {'type': 'string', 'minLength': 1, 'maxLength': 1000}}},
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'reverse', 'description': 'Return the supplied text reversed.',
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['text'],
                     'properties': {'text': {'type': 'string', 'minLength': 1, 'maxLength': 1000}}},
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
]


def call(name, arguments):
    """Validate at the server, never at the prompt: wrong arguments are an isError result, not an exception."""
    if name not in {tool['name'] for tool in TOOLS}:
        raise ValueError('unknown tool')
    if not isinstance(arguments, dict) or set(arguments) != {'text'}:
        raise ValueError('expected exactly one argument: text')
    text = arguments['text']
    if not isinstance(text, str) or not 1 <= len(text) <= 1000:
        raise ValueError('text must be a non-empty string of at most 1000 characters')
    return {'text': text if name == 'echo' else text[::-1]}


def handle(request):
    method = request.get('method')
    if method == 'initialize':
        return {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                'serverInfo': {'name': 'echo', 'version': '0.1.0'}}
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': TOOLS}
    if method == 'tools/call':
        params = request.get('params', {})
        try:
            result = call(params.get('name'), params.get('arguments', {}))
        except ValueError as exc:
            return {'isError': True, 'content': [{'type': 'text', 'text': str(exc)}]}
        return {'isError': False, 'content': [{'type': 'text', 'text': json.dumps(result, sort_keys=True)}]}
    raise KeyError(method)


for line in sys.stdin:
    try:
        request = json.loads(line)
        if not isinstance(request, dict) or request.get('jsonrpc') != '2.0':
            raise ValueError('invalid request')
    except ValueError:
        print(json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'invalid request'}}), flush=True)
        continue
    if 'id' not in request:
        continue  # notifications such as notifications/initialized need no reply
    try:
        response = {'jsonrpc': '2.0', 'id': request['id'], 'result': handle(request)}
    except KeyError:
        response = {'jsonrpc': '2.0', 'id': request['id'], 'error': {'code': -32601, 'message': 'method not found'}}
    print(json.dumps(response), flush=True)
