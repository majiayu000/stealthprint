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

    def chat(self, messages, max_tokens=1, extra=None, timeout=None):
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens}
        if extra:
            body.update(extra)
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + self.api_key,
                     "Content-Type": "application/json",
                     "User-Agent": "curl/8.7.1", "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.load(r), None
        except urllib.error.HTTPError as e:
            try:
                body_text = e.read().decode()[:600]
            except Exception:
                body_text = ""
            return None, {"http": e.code, "body": body_text}
        except Exception as e:
            return None, {"http": None, "body": str(e)}

    def prompt_tokens(self, messages, max_tokens=1, extra=None, timeout=None):
        d, err = self.chat(messages, max_tokens=max_tokens, extra=extra, timeout=timeout)
        if err:
            return None, err
        return d["usage"]["prompt_tokens"], None
