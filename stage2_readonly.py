"""Narrow read-only HA/Docker adapters; never starts or mutates services."""
import json
import subprocess
import urllib.request

from stage2_contract import ROOT, ContractError, require


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ContractError('HA redirect refused')


class HAReader:
    def __init__(self, token):
        require(isinstance(token, str) and bool(token), 'HA_TOKEN required for explicit live GET inspection')
        self._token = token

    def get(self, path):
        require(path in ('config', 'states'), 'Read-only HA route not allowed')
        request = urllib.request.Request('http://127.0.0.1:18123/api/'+path,
                                        headers={'Authorization': 'Bearer '+self._token}, method='GET')
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
                return json.load(response)
        except Exception:
            # Never surface request headers/token in artifacts.
            raise ContractError('HA GET '+path+' failed') from None


class DockerReader:
    def _run(self, *args):
        allowed = ((len(args) == 4 and args[:3] == ('compose', 'ps', '-q') and args[3] in ('broker', 'homeassistant'))
                   or (len(args) == 2 and args[0] == 'inspect' and not args[1].startswith('-'))
                   or (len(args) == 3 and args[:2] == ('image', 'inspect') and not args[2].startswith('-'))
                   or args == ('compose', 'logs', '--no-color', 'broker'))
        require(allowed, 'Docker mutation/exec/start operation refused')
        result = subprocess.run(['docker', *args], cwd=ROOT, capture_output=True, text=True, timeout=30)
        require(result.returncode == 0, 'Read-only Docker command failed: '+' '.join(args[:3]))
        return result.stdout.strip()

    def container(self, service):
        require(service in ('broker', 'homeassistant'), 'Unexpected container service')
        ids = self._run('compose', 'ps', '-q', service).splitlines()
        require(len(ids) == 1, service+' container not uniquely running')
        info = json.loads(self._run('inspect', ids[0]))[0]
        require(info['State']['Running'] is True, service+' container not running')
        image = json.loads(self._run('image', 'inspect', info['Image']))[0]
        require(bool(image.get('RepoDigests')), service+' image digests missing')
        return {'container_id': ids[0], 'running': True,
                'image': {'Id': image['Id'], 'RepoDigests': image['RepoDigests']},
                'image_reference': info.get('Config', {}).get('Image')}

    def broker_logs(self):
        return self._run('compose', 'logs', '--no-color', 'broker')


class EventSocket:
    """Only auth and event subscriptions can be sent through this adapter."""
    def __init__(self):
        import websocket
        self._timeouts = (websocket.WebSocketTimeoutException,)
        self._connection = websocket.create_connection('ws://127.0.0.1:18123/api/websocket',
                                                        timeout=10, http_proxy_host=None)

    def send(self, message):
        kind = message.get('type')
        allowed = {'auth': {'type', 'access_token'}, 'subscribe_events': {'type', 'id', 'event_type'}}
        require(kind in allowed and set(message) == allowed[kind], 'Non-observer WebSocket operation refused')
        if kind == 'subscribe_events':
            require(message['event_type'] in ('expiry_v3_received', 'expiry_v3_stage',
                    'expiry_v3_trigger_accepted', 'call_service', 'state_changed'), 'Unexpected event subscription')
        self._connection.send(json.dumps(message))

    def receive(self):
        try:
            value = self._connection.recv()
            require(bool(value), 'Observer socket closed')
            return json.loads(value)
        except self._timeouts:
            return None

    def close(self):
        self._connection.close()
