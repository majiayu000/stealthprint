"""Backtest catalog watching against archived OpenRouter catalogs.

Rebuilds each model id's first-seen date from Wayback snapshots of
/api/v1/models (plus the catalog's own `created` field) and compares it with
release dates: stealth ids via eval/reveals.json, named ids via the
model-chronicle dataset. Snapshots are sparse, so first-seen is an upper
bound and short-lived ids can be missed entirely.

    python scripts/backtest_catalog.py --cache wayback-cache --out backtest.json
"""
import argparse
import gzip
import json
import os
import re
import statistics
import sys
import time
import urllib.request
from datetime import date, datetime, timezone

CATALOG = "openrouter.ai/api/v1/models"
CHRONICLE = "https://majiayu000.github.io/model-chronicle/dataset/models.json"


def get(url, timeout=60):
    request = urllib.request.Request(url, headers={"User-Agent": "stealthprint-backtest"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    return gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body


def snapshots(cache, since="2025"):
    os.makedirs(cache, exist_ok=True)
    cdx = json.loads(get("https://web.archive.org/cdx/search/cdx?url=%s&from=%s&output=json"
                         "&fl=timestamp&filter=statuscode:200&collapse=timestamp:8" % (CATALOG, since)))
    out = {}
    for (stamp,) in cdx[1:]:
        path = os.path.join(cache, stamp + ".json")
        if not os.path.exists(path):
            try:
                body = get("https://web.archive.org/web/%sid_/https://%s" % (stamp, CATALOG))
            except OSError as error:
                print("skip %s: %s" % (stamp, error), file=sys.stderr)
                continue
            with open(path, "wb") as stream:
                stream.write(body)
            time.sleep(0.2)
        with open(path, "rb") as stream:
            body = stream.read()
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        try:
            data = json.loads(body).get("data")
        except ValueError:
            continue
        if isinstance(data, list):
            out[stamp] = data
    return out


def first_seen(snaps):
    seen = {}
    for stamp in sorted(snaps):
        day = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))
        for entry in snaps[stamp]:
            row = seen.setdefault(entry["id"], {"first": day, "last": day, "created": None})
            row["last"] = day
            if isinstance(entry.get("created"), (int, float)):
                created = datetime.fromtimestamp(entry["created"], timezone.utc).date()
                row["created"] = created
                row["first"] = min(row["first"], created)
    return seen


def norm(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def named_leads(seen, chronicle, start):
    index = {}
    for model_id, row in seen.items():
        slug = re.sub(r"-(preview|instruct|chat|it)$", "", model_id.split("/", 1)[-1].split(":")[0])
        index.setdefault(norm(slug), []).append((row["first"], model_id))
    rows = []
    for model in chronicle:
        announced = str((model.get("dates") or {}).get("announced") or "")
        if len(announced) < 10 or date.fromisoformat(announced[:10]) < start:
            continue
        hits = index.get(norm(model["id"])) or index.get(norm(model["name"]))
        if hits:
            first, catalog_id = min(hits)
            rows.append({"chronicle": model["id"], "catalog": catalog_id, "announced": announced[:10],
                         "first_seen": first.isoformat(),
                         "lead_days": (date.fromisoformat(announced[:10]) - first).days})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", default="wayback-cache")
    ap.add_argument("--since", default="2025")
    ap.add_argument("--reveals", default=os.path.join(os.path.dirname(__file__), "..", "eval", "reveals.json"))
    ap.add_argument("--chronicle", default=CHRONICLE, help="model-chronicle models.json URL or path")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    snaps = snapshots(args.cache, args.since)
    seen = first_seen(snaps)
    start = date(int(min(snaps)[:4]), int(min(snaps)[4:6]), int(min(snaps)[6:8]))
    reveals = json.load(open(args.reveals, encoding="utf-8"))["reveals"]
    stealth = []
    for item in reveals:
        row = seen.get(item["stealth"])
        lead = None
        if row and item["release"] and item["status"] == "confirmed":
            lead = (date.fromisoformat(item["release"]) - row["first"]).days
        stealth.append(dict(item, first_seen=row["first"].isoformat() if row else None,
                            last_seen=row["last"].isoformat() if row else None, lead_days=lead))

    if args.chronicle.startswith("http"):
        chronicle = json.loads(get(args.chronicle))
    else:
        chronicle = json.load(open(args.chronicle, encoding="utf-8"))
    chronicle = chronicle if isinstance(chronicle, list) else chronicle.get("models", [])
    named = named_leads(seen, chronicle, start)

    events = {}
    for row in stealth:
        if row["lead_days"] is not None:
            key = row["chronicle"] or row["revealed"]
            events[key] = max(events.get(key, -10**6), row["lead_days"])
    stealth_leads = sorted(events.values())
    report = {
        "snapshots": len(snaps), "window": [min(snaps), max(snaps)], "catalog_ids": len(seen),
        "stealth": stealth,
        "stealth_summary": {"confirmed_release_events": len(stealth_leads),
                            "median_lead_days": statistics.median(stealth_leads) if stealth_leads else None,
                            "positive": sum(lead > 0 for lead in stealth_leads)},
        "named_summary": {"matched": len(named),
                          "median_lead_days": statistics.median([r["lead_days"] for r in named]) if named else None,
                          "lead_ge_2_days": sorted((r for r in named if r["lead_days"] >= 2),
                                                   key=lambda r: -r["lead_days"])},
        "named": named,
    }
    text = json.dumps(report, ensure_ascii=False, indent=1, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as stream:
            stream.write(text)
    print(json.dumps({k: report[k] for k in ("snapshots", "window", "catalog_ids", "stealth_summary")}, indent=1))
    for row in stealth:
        print("%-34s first=%s release=%s lead=%s %s" % (row["stealth"], row["first_seen"], row["release"],
                                                       row["lead_days"], row["status"]))


if __name__ == "__main__":
    main()
