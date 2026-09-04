import json
import os
import urllib.error
import urllib.request


class ChatClient:
    """Minimal OpenAI-compatible chat client (stdlib only).

    All identity is explicit: model + base_url + api_key. No model is baked in.
    """

    def __init__(self, model, base_url=None, api_key=None, timeout=180):
        self.model = model
        self.base_url = (base_url or os.environ.get("STEALTHPRINT_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.environ.get("STEALTHPRINT_API_KEY") or self._opencode_fallback_key()
        if not self.base_url:
            raise ValueError("no base_url (pass base_url= or set STEALTHPRINT_BASE_URL)")
        if not self.api_key:
            raise ValueError("no api_key (pass api_key= or set STEALTHPRINT_API_KEY)")
        self.timeout = timeout

    @staticmethod
    def _opencode_fallback_key():
        """Convenience: reuse an existing opencode login if present (optional)."""
        path = os.path.expanduser("~/.local/share/opencode/auth.json")
        if not os.path.exists(path):
            return None
        try:
            data = json.load(open(path))
        except Exception:
            return None
        for provider in ("opencode-go", "opencode"):
            entry = data.get(provider)
            if isinstance(entry, dict) and entry.get("key"):
                return entry["key"]
        return None

    def _headers(self):
        return {"Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
                "User-Agent": "curl/8.7.1", "Accept": "*/*"}

    def request(self, method, path, body=None, timeout=None):
        """Raw HTTP to base_url+path. Returns (parsed_json_or_None, err_dict_or_None)."""
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.base_url + path, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.load(r), None
        except urllib.error.HTTPError as e:
            try:
                body_text = e.read().decode()[:1200]
            except Exception:
                body_text = ""
            return None, {"http": e.code, "body": body_text}
        except Exception as e:
            return None, {"http": None, "body": str(e)}

    def chat(self, messages, max_tokens=1, extra=None, timeout=None, model=None):
        body = {"model": model or self.model, "messages": messages, "max_tokens": max_tokens}
        if extra:
            body.update(extra)
        return self.request("POST", "/chat/completions", body=body, timeout=timeout)

    def prompt_tokens(self, messages, max_tokens=1, extra=None, timeout=None, model=None):
        d, err = self.chat(messages, max_tokens=max_tokens, extra=extra,
                           timeout=timeout, model=model)
        if err:
            return None, err
        return d["usage"]["prompt_tokens"], None

    def list_models(self, timeout=None):
        d, err = self.request("GET", "/models", timeout=timeout)
        if err:
            return None, err
        return [m.get("id") for m in (d.get("data") or []) if m.get("id")], None
