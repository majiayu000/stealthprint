"""Catalog watch: snapshot a model catalog, diff it against the previous
snapshot, and append sightings (new / removed model ids) to a JSONL log.

A sighting records when an id was observed in a catalog. It is not evidence of
who made the model; run the fingerprint layers on new ids for that.
"""
import contextlib
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .client import _NoRedirect

try:
    import fcntl
except ImportError:  # Windows has no flock; passes there are not kept apart
    fcntl = None

SOURCES = {
    "openrouter": "https://openrouter.ai/api/v1/models",
}

# Naming hints only; a hint is a reason to look, not a claim about identity.
STEALTH_HINT = re.compile(r"(^stealth/|^openrouter/(?!auto)|-alpha\b|-beta\b|cloaked|anonymous)", re.I)

# Events waiting for a webhook to accept them, beside sightings.jsonl.
WEBHOOK_PENDING = "webhook-pending.jsonl"


def resolve_source(source):
    """Return (name, url) for a known source name or an explicit catalog URL."""
    if source in SOURCES:
        return source, SOURCES[source]
    if source.startswith("https://") or source.startswith("http://"):
        name = re.sub(r"[^a-z0-9]+", "-", source.split("://", 1)[1].lower()).strip("-")
        return name, source
    raise ValueError("unknown source %r (use %s or a catalog URL)" % (source, ", ".join(SOURCES)))


_OPENER = urllib.request.build_opener(_NoRedirect())


def _open(request, timeout):
    """Open without following redirects, like ChatClient.

    urllib would resend the catalog key to whatever host the redirect names, and would
    turn a redirected webhook POST into a GET without the events that still answers 200."""
    try:
        return _OPENER.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        location = error.headers.get("Location") if 300 <= error.code < 400 else None
        if not location:
            raise
        error.close()
        raise RuntimeError("HTTP %d redirect to %s was not followed; use the final URL"
                           % (error.code, location)) from None


def fetch_catalog(url, api_key=None, timeout=60):
    """GET an OpenAI-compatible model list and return its `data` entries."""
    headers = {"Accept": "application/json", "User-Agent": "stealthprint-watch"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    with _open(urllib.request.Request(url, headers=headers), timeout) as response:
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


@contextlib.contextmanager
def _one_pass(directory):
    """Hold <directory>/pass.lock so that two passes on one source never overlap.

    Both would diff against the same latest.json, record the same events and, with on_added,
    pay to probe the same ids. The lock ends with the process that holds it."""
    with open(os.path.join(directory, "pass.lock"), "a") as stream:
        if fcntl:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("another watch pass is still running on %s; this one did nothing"
                                   % directory) from None
            except OSError:
                pass  # a filesystem without flock: not kept apart, as on Windows
        yield


def _known_ids(latest, sightings_path):
    """The ids of the last snapshot, moved on by the sightings appended after it was written.

    Those lines come from a pass that died between appending them and replacing latest.json.
    Counting them keeps the next pass from recording the same events, and probing the same
    ids, a second time."""
    known = set(latest["models"])
    covered = latest.get("sightings_bytes")
    if covered is None or not os.path.exists(sightings_path):
        return known
    with open(sightings_path, "rb") as stream:
        stream.seek(covered)
        for line in stream:
            try:
                event = json.loads(line)
                if event["event"] == "added":
                    known.add(event["model"])
                elif event["event"] == "removed":
                    known.discard(event["model"])
            except (ValueError, KeyError, TypeError):
                continue  # cut short by the crash; the diff finds that event again
    return known


def run_watch(state_dir, source="openrouter", api_key=None, fetch=fetch_catalog, now=None,
              on_added=None, verbose=True, queue_webhook=False):
    """One polling pass. The first pass stores a baseline and emits no events.

    state_dir/<source>/latest.json  last catalog index
    state_dir/<source>/sightings.jsonl  appended added/removed events
    state_dir/<source>/webhook-pending.jsonl  the same events, with queue_webhook, until deliver_webhook sends them
    on_added(ids) may return {id: evidence} which is stored on the event.
    A pass that starts while another holds the same source raises RuntimeError and does nothing.
    """
    name, url = resolve_source(source)
    observed_at = (now or datetime.now(timezone.utc)).isoformat()
    directory = os.path.join(state_dir, name)
    os.makedirs(directory, exist_ok=True)
    with _one_pass(directory):
        latest_path = os.path.join(directory, "latest.json")
        sightings_path = os.path.join(directory, "sightings.jsonl")
        current = index_catalog(fetch(url, api_key=api_key))
        if not current:
            raise RuntimeError("catalog is empty; refusing to treat every id as removed")

        baseline = not os.path.exists(latest_path)
        previous = set()
        if not baseline:
            with open(latest_path, encoding="utf-8") as stream:
                previous = _known_ids(json.load(stream), sightings_path)
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
            lines = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
            # Queued before latest.json moves on: once it has, no later pass sees these events again.
            # The queue comes first: an event already in sightings.jsonl is not found again.
            for path in ((os.path.join(directory, WEBHOOK_PENDING),) if queue_webhook else ()) + (sightings_path,):
                with open(path, "a", encoding="utf-8") as stream:
                    stream.write(lines)
        temporary = latest_path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            # sightings_bytes: how much of sightings.jsonl this snapshot accounts for, see _known_ids.
            json.dump({"source": name, "url": url, "observed_at": observed_at, "models": current,
                       "sightings_bytes": os.path.getsize(sightings_path) if os.path.exists(sightings_path) else 0},
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
    with _open(request, timeout) as response:
        return response.status


def deliver_webhook(state_dir, source, url, post=post_webhook):
    """POST every queued event, then clear the queue; return how many were sent.

    A failed POST raises and leaves the queue alone, so the next pass sends those events
    again along with its own. A receiver can therefore see an event twice.
    While a pass holds the source this raises RuntimeError: what that pass queues in the
    meantime must not be cleared unsent."""
    directory = os.path.join(state_dir, resolve_source(source)[0])
    path = os.path.join(directory, WEBHOOK_PENDING)
    if not os.path.isdir(directory):
        return 0
    with _one_pass(directory):
        if not os.path.exists(path):
            return 0
        events = []
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue  # cut short by a crash; that pass never moved latest.json, so the event comes back
        if events:
            post(url, events)
        os.remove(path)
    return len(events)
