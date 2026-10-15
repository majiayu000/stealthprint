import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone


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
    return status is None or status == 429 or (isinstance(status, int) and status >= 500)


class ChatClient:
    """Explicit endpoint client; Anthropic support is limited to text counting.

    record_path optionally appends each raw request/response to JSONL, without
    authentication headers. Redirects are returned as errors, never followed.
    """

    def __init__(self, model, base_url=None, api_key=None, timeout=180, session_id=None,
                 transport='openai', record_path=None):
        self.model = model
        if transport not in ('openai', 'anthropic'):
            raise ValueError('transport must be openai or anthropic')
        self.transport = transport
        self.base_url = (base_url or os.environ.get('STEALTHPRINT_BASE_URL') or '').rstrip('/')
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
        return h

    def request(self, method, path, body=None, timeout=None):
        """Return (JSON object, None) or (None, error), retaining original errors."""
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base_url + path, data=data,
                                     headers=self._headers(), method=method)
        started = datetime.now(timezone.utc).isoformat()
        clock = time.monotonic()
        parsed = error = status = None
        raw = ''
        try:
            with self._opener.open(req, timeout=self.timeout if timeout is None else timeout) as response:
                status = response.status
                raw = response.read().decode('utf-8')
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                error = {'http': status, 'body': 'expected a JSON object', 'detail': parsed}
            elif parsed.get('error') is not None:
                error = {'http': status, 'body': raw, 'detail': parsed['error']}
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                raw = exc.read().decode('utf-8', errors='replace')
            finally:
                exc.close()
            error = {'http': status, 'body': raw}
            if exc.headers.get('Location'):
                error['location'] = exc.headers['Location']
        except (OSError, ValueError, urllib.error.URLError) as exc:
            error = {'http': status, 'body': str(exc)}
        if self.record_path:
            row = {'started_at': started, 'duration_seconds': time.monotonic() - clock,
                   'method': method, 'url': self.base_url + path, 'transport': self.transport,
                   'session_id': self.session_id, 'request': body, 'http': status,
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
        if not isinstance(usage, dict) or not isinstance(usage.get(key), (int, float)):
            return None, {'http': 200, 'body': 'no %s in usage' % key, 'detail': d}
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
