"""Official ChatGPT plan OAuth. Tokens stay in owner-only local files."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
import uuid
from urllib.parse import urlencode

import jwt
import requests

AUTH = 'https://auth.openai.com'
API = 'https://api.openai.com/v1'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'


class StudyError(Exception):
    pass


def private_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(6))
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ChatGPTConnection:
    def __init__(self, root, port):
        self.root = Path(root)
        self.callback_uri = f'http://127.0.0.1:{port}/oauth/chatgpt/callback'
        self.lock = threading.RLock()
        self.pending = None
        self.http = requests.Session()

    def load(self):
        path = self.root/'accounts.json'
        return json.loads(path.read_text()) if path.exists() else {'active': None, 'accounts': {}}

    def status(self):
        with self.lock:
            data = self.load()
            current = data['accounts'].get(data['active'], {})
            return {'connected': bool(current.get('access_token')),
                    'plan_enabled': 'chatgpt.tokens.use.direct' in current.get('scopes', []),
                    'email': current.get('email', ''),
                    'accounts': [{'id': key, 'email': val.get('email', '')} for key, val in data['accounts'].items()],
                    'active': data['active']}

    def authorize(self, account=None):
        with self.lock:
            data = self.load()
            if account and account not in data['accounts']:
                raise StudyError('Unknown saved account.')
            profile = data['accounts'].get(account, {})
            host_path = self.root/'host.json'
            if not host_path.exists():
                private_write(host_path, {'id': 'urn:uuid:' + str(uuid.uuid4())})
            host = json.loads(host_path.read_text())['id']
            verifier = secrets.token_urlsafe(48)
            pending = {'state': secrets.token_urlsafe(32), 'nonce': secrets.token_urlsafe(32),
                       'verifier': verifier, 'expires': time.time()+600, 'client_id': account,
                       'subject': profile.get('subject')}
            self.pending = pending
            params = {'client_id': account or 'dynamic_agent_client', 'ext_agent_host_id': host,
                      'response_type': 'code', 'redirect_uri': self.callback_uri, 'scope': SCOPES,
                      'resource': API, 'state': pending['state'], 'nonce': pending['nonce'],
                      'code_challenge_method': 'S256',
                      'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')}
            if not account:
                params['agent_name_hint'] = 'Coursewell'
            elif profile.get('id_token'):
                params['id_token_hint'] = profile['id_token']
            return AUTH+'/api/accounts/authorize?'+urlencode(params)

    def token_request(self, fields):
        try:
            response = self.http.post(AUTH+'/api/accounts/oauth/token', data=fields, timeout=25)
            if response.status_code != 200:
                raise StudyError('ChatGPT authorization could not complete. Try signing in again.')
            return response.json()
        except (requests.RequestException, ValueError):
            raise StudyError('Could not reach ChatGPT authorization. Check your connection and retry.') from None

    def callback(self, query):
        with self.lock:
            pending = self.pending
            state = query.get('state', [''])[0]
            if not pending or pending['expires'] < time.time() or not secrets.compare_digest(state, pending['state']):
                raise StudyError('Sign-in expired or could not be verified. Start again from Study.')
            self.pending = None
            if query.get('error'):
                raise StudyError('ChatGPT sign-in was not approved. You can try again from Study.')
            client = query.get('client_id', [pending['client_id']])[0]
            if not client or client == 'dynamic_agent_client' or (pending['client_id'] and client != pending['client_id']):
                raise StudyError('ChatGPT returned an unexpected account registration. Start again.')
            code = query.get('code', [''])[0]
            if not code:
                raise StudyError('ChatGPT did not return an authorization code.')
            # Retain issued registration even if exchange fails; no token is trusted yet.
            data = self.load()
            data['accounts'].setdefault(client, {})
            private_write(self.root/'accounts.json', data)
            tokens = self.token_request({'grant_type': 'authorization_code', 'client_id': client,
                                        'code': code, 'code_verifier': pending['verifier'],
                                        'redirect_uri': self.callback_uri, 'resource': API})
            try:
                key = jwt.PyJWKClient(AUTH+'/.well-known/jwks.json', timeout=20).get_signing_key_from_jwt(tokens['id_token'])
                claims = jwt.decode(tokens['id_token'], key.key, algorithms=['RS256'], audience=client,
                                    issuer=AUTH, options={'require': ['exp', 'iss', 'aud', 'sub', 'nonce']})
                if not secrets.compare_digest(claims['nonce'], pending['nonce']):
                    raise ValueError('nonce mismatch')
                if pending['subject'] and claims['sub'] != pending['subject']:
                    raise ValueError('account mismatch')
                if not tokens.get('access_token'):
                    raise ValueError('missing access token')
            except Exception:
                raise StudyError('The ChatGPT account identity could not be verified. No connection was activated.') from None
            data['accounts'][client] = {**tokens, 'subject': claims['sub'], 'email': claims.get('email', ''),
                                         'scopes': tokens.get('scope', '').split(),
                                         'expires_at': time.time()+int(tokens.get('expires_in', 3600))}
            data['active'] = client
            private_write(self.root/'accounts.json', data)

    def access(self):
        with self.lock:
            data = self.load()
            client = data['active']
            profile = data['accounts'].get(client, {})
            if not profile.get('access_token') or 'chatgpt.tokens.use.direct' not in profile.get('scopes', []):
                raise StudyError('Connect ChatGPT and allow plan usage before asking a question.')
            if profile.get('expires_at', 0) < time.time()+90:
                if not profile.get('refresh_token'):
                    raise StudyError('Your ChatGPT connection expired. Sign in again.')
                tokens = self.token_request({'grant_type': 'refresh_token', 'client_id': client,
                                            'refresh_token': profile['refresh_token'], 'resource': API})
                profile.update(tokens)
                if 'scope' in tokens:
                    profile['scopes'] = tokens['scope'].split()
                profile['expires_at'] = time.time()+int(tokens.get('expires_in', 3600))
                private_write(self.root/'accounts.json', data)
                if 'chatgpt.tokens.use.direct' not in profile['scopes']:
                    raise StudyError('ChatGPT plan permission is no longer enabled.')
            return profile['access_token']

    def models(self):
        try:
            response = self.http.get(API+'/models', headers={'Authorization': 'Bearer '+self.access()}, timeout=25)
            if response.status_code != 200:
                raise StudyError('Could not load your ChatGPT models. Reconnect or check plan access.')
            return [{'id': m['slug'], 'name': m.get('display_name', m['slug'])}
                    for m in response.json().get('models', []) if m.get('visibility') == 'list']
        except (requests.RequestException, ValueError):
            raise StudyError('Could not load models. Check your internet connection.') from None

    def complete(self, model, instructions, prompt, usage_callback=None):
        if model not in {m['id'] for m in self.models()}:
            raise StudyError('Choose a model from your connected account.')
        try:
            with self.http.post(API+'/responses', headers={'Authorization': 'Bearer '+self.access()},
                                json={'model': model, 'instructions': instructions,
                                      'input': [{'role': 'user', 'content': prompt}],
                                      'store': False, 'stream': True}, stream=True, timeout=(15, 120)) as response:
                if response.status_code != 200:
                    if response.status_code==429:
                        raise StudyError('ChatGPT plan rate or usage limit reached. Saved reviews are retained; retry later. No paid API fallback was used.')
                    if response.status_code in (401,403):
                        raise StudyError('ChatGPT authorization was rejected. Reconnect your account. No paid API fallback was used.')
                    raise StudyError(f'ChatGPT request failed (HTTP {response.status_code}). Saved reviews are retained; retry later. No paid API fallback was used.')
                text = ''
                for line in response.iter_lines():
                    if not line.startswith(b'data: '):
                        continue
                    payload = line[6:]
                    if payload == b'[DONE]':
                        continue
                    event = json.loads(payload)
                    kind = event.get('type')
                    if kind == 'response.output_text.delta':
                        text += event.get('delta', '')
                        if len(text) > 100000:
                            raise StudyError('Answer exceeded the local display limit. Ask a narrower question.')
                    elif kind == 'response.completed':
                        if usage_callback:
                            usage_callback(event.get('response', {}).get('usage'))
                        return text
                    elif kind in ('response.failed', 'response.incomplete', 'error'):
                        raise StudyError('ChatGPT did not complete the answer. Check your plan allowance or retry later; no paid fallback was used.')
                raise StudyError('The answer was interrupted. No complete answer was saved.')
        except (requests.RequestException, ValueError):
            raise StudyError('ChatGPT connection interrupted. Please retry when connected.') from None

    def disconnect(self):
        with self.lock:
            data = self.load()
            profile = data['accounts'].get(data['active'], {})
            revoked = not profile.get('refresh_token')
            if profile.get('refresh_token'):
                try:
                    result = self.http.post(AUTH+'/api/accounts/oauth/revoke', data={
                        'token': profile['refresh_token'], 'token_type_hint': 'refresh_token',
                        'client_id': data['active']}, timeout=20)
                    revoked = result.status_code == 200
                except requests.RequestException:
                    pass
            for key in ('access_token', 'refresh_token', 'id_token'):
                profile.pop(key, None)
            private_write(self.root/'accounts.json', data)
            return {'message': 'Disconnected.' if revoked else 'Disconnected locally. Remote revocation was not confirmed; remove access in ChatGPT Settings.'}
