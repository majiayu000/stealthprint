import json
import http.client
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

with open(os.path.join(_DATA_DIR, 'opencode_free_system.txt')) as _f:
    # System prompt whitelisted by OpenCode Zen's free-tier client check
    # (extracted verbatim from an opencode CLI title-generator request).
    OPENCODE_FREE_SYSTEM = _f.read()

# Free-tier requests pass the client fingerprint check only when the
# session headers below are present (any strict subset is rejected).
OPENCODE_FREE_HEADERS = {
    'User-Agent': 'opencode/latest/2.0.29/cli',
    'x-opencode-client': 'cli',
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def require_openai(client):
    """Fail before a probe sends a request using an unsupported wire format."""
    if getattr(client, 'transport', 'openai') == 'anthropic':
        raise ValueError('This probe requires OpenAI chat format; Anthropic supports text counting only.')


def retryable(error):
    """Only retry transport failures, throttling and server failures."""
    status = (error or {}).get('http')
    return bool((error or {}).get('transport_error')) or status is None or status == 429 or (isinstance(status, int) and status >= 500)


class ChatClient:
    """Explicit endpoint client; Anthropic support is limited to text counting.

    record_path optionally appends each raw request/response to JSONL, without
    authentication headers. Redirects are returned as errors, never followed.

    shell='opencode-free' targets OpenCode Zen /zen/v1 free endpoints: requests
    are wrapped (whitelisted system prompt + stream) so the server's
    client-fingerprint check lets them through.
    """

    def __init__(self, model, base_url=None, api_key=None, timeout=180, session_id=None,
                 transport='openai', record_path=None, shell=None):
        self.model = model
        if transport not in ('openai', 'anthropic'):
            raise ValueError('transport must be openai or anthropic')
        self.transport = transport
        self.base_url = (base_url or os.environ.get('STEALTHPRINT_BASE_URL') or '').rstrip('/')
        self.shell = shell
        if shell == 'opencode-free':
            # The free tier accepts the sentinel key 'public' (anonymous tier).
            api_key = api_key or 'public'
            # The server tracks session ids minted by the real client; a random
            # id (e.g. uuid4) is rejected. Reuse one from an actual opencode run.
            if not session_id or not session_id.startswith('ses_'):
                raise ValueError('opencode-free shell needs a session id from a real '
                                 'opencode run (ses_..., pass session_id=)')
        self.api_key = api_key or os.environ.get('STEALTHPRINT_API_KEY')
        if not self.base_url:
            raise ValueError('no base_url (pass base_url= or set STEALTHPRINT_BASE_URL)')
        if not self.api_key:
            raise ValueError('no api_key (pass api_key= or set STEALTHPRINT_API_KEY)')
        self.session_id = session_id or str(uuid.uuid4())
        self.timeout = timeout
        self.record_path = record_path
        self._record_lock = threading.Lock()
        self._opener = urllib.request.build_opener(_NoRedirect())
        if record_path:
            # Check the destination before any billable request.
            with open(record_path, 'a', encoding='utf-8'):
                pass

    def _headers(self):
        h = {'Content-Type': 'application/json', 'x-session-id': self.session_id,
             'User-Agent': 'stealthprint/0.1.0', 'Accept': 'application/json'}
        if self.transport == 'anthropic':
            h['x-api-key'] = self.api_key
            h['anthropic-version'] = '2023-06-01'
        else:
            h['Authorization'] = 'Bearer ' + self.api_key
        if self.shell == 'opencode-free':
            h.update(OPENCODE_FREE_HEADERS)
            h.update({
                'x-opencode-project': '876eef6870aa3997dbcab363fa401c275b9c4ff4',
                'x-opencode-session': self.session_id,
                'x-opencode-session-id': self.session_id,
                'x-session-affinity': self.session_id,
            })
        return h

    def _wrap_body(self, body):
        """Prepend the shell system prompt and force streaming (free-tier check)."""
        wrapped = dict(body)
        messages = list(wrapped.get('messages') or [])
        if not (messages and messages[0].get('role') == 'system'
                and messages[0].get('content') == OPENCODE_FREE_SYSTEM):
            messages = [{'role': 'system', 'content': OPENCODE_FREE_SYSTEM}] + messages
        wrapped['messages'] = messages
        wrapped['stream'] = True
        wrapped['stream_options'] = {'include_usage': True}
        # The free deployment reasons by default; the title-generator shell then
        # hijacks output (model narrates a 'title'). Disabling reasoning restores
        # verbatim answers and matches the paid line's default behavior.
        wrapped.setdefault('reasoning_effort', 'none')
        return wrapped

    def request(self, method, path, body=None, timeout=None):
        """Return (JSON object, None) or (None, error), retaining original errors."""
        if self.shell == 'opencode-free' and method == 'POST' and body is not None:
            body = self._wrap_body(body)
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base_url + path, data=data,
                                     headers=self._headers(), method=method)
        started = datetime.now(timezone.utc).isoformat()
        clock = time.monotonic()
        parsed = error = status = None
        raw = ''
        try:
            try:
                response = self._opener.open(req, timeout=self.timeout if timeout is None else timeout)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                status = response.status
                raw = response.read().decode('utf-8', errors='replace')
                if isinstance(response, urllib.error.HTTPError):
                    error = {'http': status, 'body': raw}
                    if response.headers.get('Location'):
                        error['location'] = response.headers['Location']
            if error is None:
                if self.shell == 'opencode-free' and method == 'POST':
                    parsed = _sse_to_chat_response(raw)
                else:
                    parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    error = {'http': status, 'body': 'expected a JSON object', 'detail': parsed}
                elif parsed.get('error') is not None:
                    error = {'http': status, 'body': raw, 'detail': parsed['error']}
        except (OSError, ValueError, http.client.HTTPException) as exc:
            if isinstance(exc, http.client.IncompleteRead):
                raw = exc.partial.decode('utf-8', errors='replace')
            error = {'http': status, 'body': '%s: %s' % (type(exc).__name__, exc)}
            if isinstance(exc, (OSError, http.client.HTTPException)):
                error['transport_error'] = True
        if self.record_path:
            row = {'started_at': started, 'duration_seconds': time.monotonic() - clock,
                   'method': method, 'url': self.base_url + path, 'transport': self.transport,
                   'session_id': self.session_id, 'shell': self.shell, 'request': body, 'http': status,
                   'response': parsed, 'raw_response': raw, 'error': error}
            with self._record_lock, open(self.record_path, 'a', encoding='utf-8') as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        return (None, error) if error else (parsed, None)

    def chat(self, messages, max_tokens=1, extra=None, timeout=None, model=None):
        body = {'model': model or self.model, 'messages': messages, 'max_tokens': max_tokens}
        if extra:
            body.update(extra)
        if self.transport == 'anthropic':
            if any(not isinstance(m.get('content'), str) or m.get('role') not in ('user', 'assistant')
                   for m in messages) or extra:
                raise ValueError('Anthropic transport supports plain user/assistant text counting only')
        path = '/messages' if self.transport == 'anthropic' else '/chat/completions'
        d, error = self.request('POST', path, body=body, timeout=timeout)
        if error:
            return None, error
        usage = d.get('usage')
        key = 'input_tokens' if self.transport == 'anthropic' else 'prompt_tokens'
        if not isinstance(usage, dict) or type(usage.get(key)) is not int or usage[key] < 0:
            return None, {'http': 200, 'body': 'invalid or missing %s in usage' % key, 'detail': d}
        if self.transport == 'anthropic':
            for field in ('cache_read_input_tokens', 'cache_creation_input_tokens'):
                value = usage.get(field, 0)
                if type(value) is not int or value < 0:
                    return None, {'http': 200, 'body': 'invalid %s in usage' % field, 'detail': d}
        if self.transport == 'openai':
            choices = d.get('choices')
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict) or not isinstance(choices[0].get('message'), dict):
                return None, {'http': 200, 'body': 'no message in choices', 'detail': d}
        return d, None

    def prompt_tokens(self, messages, max_tokens=1, extra=None, timeout=None, model=None):
        d, err = self.chat(messages, max_tokens=max_tokens, extra=extra, timeout=timeout, model=model)
        if err:
            return None, err
        key = 'input_tokens' if self.transport == 'anthropic' else 'prompt_tokens'
        usage = d.get('usage') or {}
        pt = usage.get(key)
        if pt is None:
            return None, {'http': 200, 'body': 'no %s in usage' % key}
        if self.transport == 'anthropic':
            pt += (usage.get('cache_read_input_tokens') or 0) + (usage.get('cache_creation_input_tokens') or 0)
        return pt, None

    def list_models(self, timeout=None):
        d, err = self.request('GET', '/models', timeout=timeout)
        if err:
            return None, err
        if not isinstance(d.get('data'), list):
            return None, {'http': 200, 'body': 'no model list in data', 'detail': d}
        return [m['id'] for m in d['data'] if isinstance(m, dict) and m.get('id')], None


def _sse_to_chat_response(raw):
    """Aggregate an SSE chat stream into a non-streaming response shape."""
    content, reasoning, usage, finish = [], [], None, None
    for line in raw.splitlines():
        if not line.startswith('data:'):
            continue
        payload = line[5:].strip()
        if not payload or payload == '[DONE]':
            continue
        try:
            chunk = json.loads(payload)
        except ValueError:
            continue
        if chunk.get('usage'):
            usage = chunk['usage']
        for choice in chunk.get('choices') or []:
            delta = choice.get('delta') or {}
            if delta.get('content'):
                content.append(delta['content'])
            if delta.get('reasoning_content'):
                reasoning.append(delta['reasoning_content'])
            if choice.get('finish_reason'):
                finish = choice['finish_reason']
    message = {'role': 'assistant', 'content': ''.join(content)}
    if reasoning:
        message['reasoning_content'] = ''.join(reasoning)
    return {'choices': [{'message': message, 'finish_reason': finish}],
            'usage': usage or {}}
