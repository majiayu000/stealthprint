"""Catalog watch: snapshot a model catalog, diff it against the previous
snapshot, and append sightings (new / removed model ids) to a JSONL log.

A sighting records when an id was observed in a catalog. It is not evidence of
who made the model; run the fingerprint layers on new ids for that.
"""
import json
import os
import re
import urllib.request
from datetime import datetime, timezone

SOURCES = {
    "openrouter": "https://openrouter.ai/api/v1/models",
}

# Naming hints only; a hint is a reason to look, not a claim about identity.
STEALTH_HINT = re.compile(r"(^stealth/|^openrouter/(?!auto)|-alpha\b|-beta\b|cloaked|anonymous)", re.I)


def resolve_source(source):
    """Return (name, url) for a known source name or an explicit catalog URL."""
    if source in SOURCES:
        return source, SOURCES[source]
    if source.startswith("https://") or source.startswith("http://"):
        name = re.sub(r"[^a-z0-9]+", "-", source.split("://", 1)[1].lower()).strip("-")
        return name, source
    raise ValueError("unknown source %r (use %s or a catalog URL)" % (source, ", ".join(SOURCES)))


def fetch_catalog(url, api_key=None, timeout=60):
    """GET an OpenAI-compatible model list and return its `data` entries."""
    headers = {"Accept": "application/json", "User-Agent": "stealthprint-watch"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise RuntimeError("catalog response has no data list")
    return data


def index_catalog(entries):
    """Map id -> minimal metadata kept in snapshots."""
    out = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            continue
        created = entry.get("created")
        out[entry["id"]] = {
            "name": entry.get("name"),
            "created": created if isinstance(created, (int, float)) and not isinstance(created, bool) else None,
            "context_length": entry.get("context_length"),
        }
    return out


def diff_catalogs(previous, current):
    """Return (added, removed) sorted id lists."""
    return sorted(set(current) - set(previous)), sorted(set(previous) - set(current))


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts is not None else None


def run_watch(state_dir, source="openrouter", api_key=None, fetch=fetch_catalog, now=None,
              on_added=None, verbose=True):
    """One polling pass. The first pass stores a baseline and emits no events.

    state_dir/<source>/latest.json  last catalog index
    state_dir/<source>/sightings.jsonl  appended added/removed events
    on_added(ids) may return {id: evidence} which is stored on the event.
    """
    name, url = resolve_source(source)
    observed_at = (now or datetime.now(timezone.utc)).isoformat()
    directory = os.path.join(state_dir, name)
    os.makedirs(directory, exist_ok=True)
    latest_path = os.path.join(directory, "latest.json")
    current = index_catalog(fetch(url, api_key=api_key))
    if not current:
        raise RuntimeError("catalog is empty; refusing to treat every id as removed")

    baseline = not os.path.exists(latest_path)
    previous = {}
    if not baseline:
        with open(latest_path, encoding="utf-8") as stream:
            previous = json.load(stream)["models"]
    added, removed = ([], []) if baseline else diff_catalogs(previous, current)

    evidence = (on_added(added) or {}) if (on_added and added) else {}
    events = []
    for model in added:
        meta = current[model]
        event = {"event": "added", "source": name, "model": model, "observed_at": observed_at,
                 "catalog_created": _iso(meta["created"]), "name": meta["name"],
                 "context_length": meta["context_length"],
                 "stealth_hint": bool(STEALTH_HINT.search(model))}
        if model in evidence:
            event["evidence"] = evidence[model]
        events.append(event)
    for model in removed:
        events.append({"event": "removed", "source": name, "model": model, "observed_at": observed_at,
                       "stealth_hint": bool(STEALTH_HINT.search(model))})

    if events:
        with open(os.path.join(directory, "sightings.jsonl"), "a", encoding="utf-8") as stream:
            for event in events:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    temporary = latest_path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump({"source": name, "url": url, "observed_at": observed_at, "models": current},
                  stream, ensure_ascii=False)
    os.replace(temporary, latest_path)

    if verbose:
        if baseline:
            print("%s: baseline stored, %d models" % (name, len(current)))
        for event in events:
            mark = " [stealth hint]" if event["stealth_hint"] else ""
            print("%s %s %s%s" % (name, "+" if event["event"] == "added" else "-", event["model"], mark))
    return {"source": name, "baseline": baseline, "models": len(current),
            "added": added, "removed": removed, "events": events}


def post_webhook(url, events, timeout=30):
    """POST events as JSON to a user-configured webhook."""
    body = json.dumps({"events": events}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status
