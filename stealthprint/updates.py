"""Public model observations, retained evidence, and independent source health.

Only public metadata is fetched. No inference calls or remote code execution.
"""

import ast
import base64
import gzip
import hashlib
from http.client import HTTPException
import json
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import re
import urllib.error
import urllib.request
import uuid
from urllib.parse import quote, urlsplit


SOURCES = (
    {"id": "aitracker-feed", "name": "AI Tracker 消息", "kind": "feed", "interval": 300,
     "url": "https://live.aitracker.bot/feeds/all.json"},
    {"id": "aitracker-catalog", "name": "AI Tracker 目录", "kind": "tracker", "interval": 900,
     "url": "https://live.aitracker.bot/api/model-pages"},
    {"id": "openrouter", "name": "OpenRouter · 文本输出", "kind": "openrouter", "interval": 300,
     "url": "https://openrouter.ai/api/v1/models"},
    {"id": "models-dev", "name": "models.dev · OpenCode", "kind": "models-dev", "interval": 900,
     "url": "https://models.dev/api.json"},
    {"id": "huggingface-deepseek", "name": "Hugging Face · DeepSeek", "kind": "huggingface", "interval": 900,
     "url": "https://huggingface.co/api/models?author=deepseek-ai&sort=lastModified&direction=-1&limit=100&full=true"},
    {"id": "openai-sdk", "name": "OpenAI SDK", "kind": "sdk", "interval": 900,
     "url": "https://api.github.com/repos/openai/openai-python/contents/src/openai/types/shared/chat_model.py?ref=main"},
    {"id": "anthropic-sdk", "name": "Anthropic SDK", "kind": "sdk", "interval": 900,
     "url": "https://api.github.com/repos/anthropics/anthropic-sdk-python/contents/src/anthropic/types/model.py?ref=main"},
)
HEADER_NAMES = {"content-type", "date", "age", "etag", "last-modified", "cache-control", "link", "retry-after"}


def timestamp(value):
    return value.astimezone(timezone.utc).isoformat()


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp must be a string")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp has no timezone")
    return result


def empty_state():
    return {"checked_at": None, "sources": {}, "events": []}


def load_state(directory):
    path = Path(directory) / "state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else empty_state()


def _fetch(url, headers):
    request = urllib.request.Request(url, headers={
        "Accept": "application/json", "User-Agent": "stealthprint-monitor/0.1", **headers,
    })
    try:
        response = urllib.request.urlopen(request, timeout=25)
    except urllib.error.HTTPError as error:
        if error.code != 304:
            raise
        response = error
    with response:
        body = response.read(20 * 1024 * 1024 + 1)
        if len(body) > 20 * 1024 * 1024:
            raise ValueError("Source response exceeds 20 MiB")
        return response.code, {k.lower(): v for k, v in response.headers.items()
                               if k.lower() in HEADER_NAMES}, body


def _http_url(value):
    if not isinstance(value, str):
        raise ValueError("Source URL must be a string")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("Source item must link to an HTTP(S) URL")
    return value


def _index(rows):
    result = {}
    for identity, record in rows:
        if not isinstance(identity, str) or not identity or identity in result:
            raise ValueError("Source has an empty or duplicate identifier")
        if not isinstance(record["name"], str) or not record["name"]:
            raise ValueError("Source item has no name")
        _http_url(record["url"])
        result[identity] = record
    return result


def _normalize(kind, payload):
    if not isinstance(payload, list if kind == "huggingface" else dict):
        raise ValueError("Source response has an invalid top-level shape")
    if kind == "feed":
        rows = []
        for item in payload["items"]:
            parse_time(item["date_published"])
            if not isinstance(item["content_text"], str) or not isinstance(item["tags"], list):
                raise ValueError("Feed item has invalid text or tags")
            rows.append((item["id"], {"name": item["title"], "url": item["url"],
                         "published_at": item["date_published"], "summary": item["content_text"],
                         "tags": item["tags"]}))
        return _index(rows)
    if kind == "tracker":
        return _index((item["slug"], {"name": item["model"], "maker": item.get("maker"),
                      "url": "https://live.aitracker.bot" + item["path"], "stage": item["stage"]})
                      for item in payload["pages"])
    if kind == "openrouter":
        links = payload.get("links", {})
        if not isinstance(links, dict):
            raise ValueError("OpenRouter pagination metadata is invalid")
        if links.get("next"):
            raise ValueError("OpenRouter returned an incomplete paginated catalog")
        if payload.get("total_count", len(payload["data"])) != len(payload["data"]):
            raise ValueError("OpenRouter catalog count does not match total_count")
        fields = ("canonical_slug", "alias_target", "context_length", "architecture", "pricing",
                  "supported_parameters", "expiration_date")
        return _index((item["id"], {"name": item["name"],
                      "url": "https://openrouter.ai/" + quote(item["id"], safe="/:"),
                      **{key: sorted(item[key]) if key == "supported_parameters" and isinstance(item.get(key), list)
                         else item.get(key) for key in fields}}) for item in payload["data"])
    if kind == "models-dev":
        models = payload["opencode"]["models"]
        if not isinstance(models, dict):
            raise ValueError("OpenCode catalog must be an object")
        fields = ("status", "limit", "modalities", "cost", "reasoning", "canonical_model_id")
        return _index((key, {"name": item["name"], "url": "https://models.dev/#opencode",
                      **{field: item.get(field) for field in fields}}) for key, item in models.items())
    if kind == "huggingface":
        return _index((item["id"], {"name": item["id"],
                      "url": "https://huggingface.co/" + quote(item["id"], safe="/"),
                      "revision": item["sha"], "pipeline_tag": item.get("pipeline_tag"),
                      "gated": item.get("gated"), "files": sorted(file["rfilename"] for file in item["siblings"])})
                      for item in payload)
    if kind == "sdk":
        if payload["encoding"] != "base64":
            raise ValueError("Unsupported SDK file encoding")
        tree = ast.parse(base64.b64decode(payload["content"]).decode("utf-8"))
        models = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "Literal":
                for child in ast.walk(node.slice):
                    if isinstance(child, ast.Constant) and isinstance(child.value, str):
                        models.add(child.value)
        if not models:
            raise ValueError("SDK file contains no model literals")
        notes = [node.value.value for node in tree.body if isinstance(node, ast.Expr)
                 and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)]
        return _index([("model-types", {"name": payload["name"], "url": payload["html_url"],
                        "models": sorted(models), "notes": notes})])
    raise ValueError("Unknown source kind: " + kind)


def _collect(source, previous):
    request_headers = {}
    if source["kind"] != "huggingface":
        if previous.get("etag"):
            request_headers["If-None-Match"] = previous["etag"]
        elif previous.get("last_modified"):
            request_headers["If-Modified-Since"] = previous["last_modified"]
    url, seen, responses, records = source["url"], set(), [], []
    while url:
        if url in seen or len(seen) >= 50:
            raise ValueError("Pagination did not finish within 50 unique pages")
        seen.add(url)
        code, headers, body = _fetch(url, request_headers if not responses else {})
        if code == 304:
            if responses or "inventory" not in previous:
                raise ValueError("Received 304 without a complete stored baseline")
            return None, headers, None
        if code != 200:
            raise ValueError("Unexpected source HTTP status: " + str(code))
        text = body.decode("utf-8")
        data = json.loads(text)
        responses.append({"url": url, "status": code, "headers": headers, "body": text})
        if source["kind"] != "huggingface":
            return _normalize(source["kind"], data), headers, {"responses": responses}
        records.extend(data)
        link = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("link", ""))
        url = link.group(1) if link else None
        if url and (urlsplit(url).scheme, urlsplit(url).netloc, urlsplit(url).path) != (
                urlsplit(source["url"]).scheme, urlsplit(source["url"]).netloc, urlsplit(source["url"]).path):
            raise ValueError("Pagination left the source endpoint")
    return _normalize(source["kind"], records), responses[0]["headers"], {"responses": responses}


def _save_evidence(directory, envelope):
    body = json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    relative = "evidence/" + digest + ".json.gz"
    path = directory / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    compressed = gzip.compress(body, mtime=0)
    try:
        if path.read_bytes() == compressed:
            return relative
    except FileNotFoundError:
        pass
    # Publish only complete bytes, including when repairing an older partial file.
    # A unique sibling created like the final file keeps umask-based permissions.
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(compressed)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return relative


def _events(source, before, after, observed_at, old_evidence, evidence, previous_events):
    latest = {item["entity_id"]: item for item in previous_events if item["source_id"] == source["id"]}
    events = []
    for identity in sorted(before.keys() | after.keys()):
        old, new = before.get(identity), after.get(identity)
        if old == new:
            continue
        if old is None:
            kind = "reported" if source["kind"] == "feed" else "restored" if latest.get(identity, {}).get("kind") == "missing" else "added"
        else:
            kind = "missing" if new is None else "changed"
        event = {"source_id": source["id"], "entity_id": identity, "kind": kind,
                 "observed_at": observed_at, "published_at": (new or old).get("published_at"),
                 "before": old, "after": new,
                 "before_evidence": latest.get(identity, {}).get("evidence", old_evidence) if source["kind"] == "feed" else old_evidence,
                 "evidence": evidence}
        event["id"] = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()[:24]
        events.append(event)
    return events


def refresh_monitor(directory, sources=SOURCES, now=None):
    """Commit one local snapshot; one broken source cannot erase another's data."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc)
    observed_at = timestamp(now)
    state = load_state(directory)
    for source in sources:
        previous = state["sources"].get(source["id"], {})
        if previous.get("next_check_at") and parse_time(previous["next_check_at"]) > now:
            continue
        status = {**previous, "name": source["name"], "url": source["url"], "kind": source["kind"],
                  "interval": source["interval"], "checked_at": observed_at,
                  "next_check_at": timestamp(now + timedelta(seconds=source["interval"]))}
        try:
            inventory, headers, envelope = _collect(source, previous)
            if inventory is not None:
                before = previous.get("inventory", {})
                if source["kind"] == "feed":
                    inventory = {**before, **inventory}
                if inventory != before or "inventory" not in previous:
                    evidence = _save_evidence(directory, envelope)
                    if "inventory" in previous or source["kind"] == "feed":
                        state["events"].extend(_events(source, before, inventory, observed_at,
                                                      previous.get("evidence"), evidence, state["events"]))
                    status.update(inventory=inventory, evidence=evidence, last_changed_at=observed_at)
                status["baseline_at"] = previous.get("baseline_at", observed_at)
                status["etag"] = headers.get("etag")
                status["last_modified"] = headers.get("last-modified")
            status.update(last_success_at=observed_at, error=None)
        except (OSError, HTTPException, ValueError, KeyError, TypeError, SyntaxError) as error:
            status["error"] = type(error).__name__ + ": " + str(error)
            if isinstance(error, urllib.error.HTTPError):
                retry = error.headers.get("Retry-After")
                if retry:
                    try:
                        retry_at = now + timedelta(seconds=int(retry)) if retry.isdigit() else parsedate_to_datetime(retry)
                        status["next_check_at"] = timestamp(max(parse_time(status["next_check_at"]), retry_at))
                    except (ValueError, TypeError, OverflowError):
                        pass  # Malformed upstream Retry-After does not replace the ordinary interval.
                error.close()
        state["sources"][source["id"]] = status
    state["checked_at"] = observed_at
    temporary = directory / "state.json.tmp"
    try:
        temporary.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        temporary.replace(directory / "state.json")
    finally:
        temporary.unlink(missing_ok=True)
    return state


def group_updates(snapshot):
    """Read the existing dated Tracker history without inventing observation dates."""
    pages = {"https://live.aitracker.bot" + page["path"]: page for page in snapshot["catalog"]["pages"]}
    groups = {}
    for item in snapshot["feed"]["items"]:
        url = _http_url(item["url"])
        page = pages.get(url)
        if url not in groups:
            groups[url] = {"url": url, "model": page["model"] if page else item["title"],
                           "maker": (page.get("maker") or "来源未注明") if page else "来源未注明", "events": []}
        groups[url]["events"].append(item)
    for group in groups.values():
        group["events"].sort(key=lambda item: item["date_published"], reverse=True)
    return sorted(groups.values(), key=lambda group: group["events"][0]["date_published"], reverse=True)
