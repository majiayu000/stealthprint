"""Collect public metadata into the persistent .monitor-data checkout."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stealthprint.updates import refresh_monitor


if __name__ == "__main__":
    state = refresh_monitor(ROOT / ".monitor-data")
    for identity, source in state["sources"].items():
        print("%s: %s; last success: %s; baseline: %d records" % (
            identity, source["error"] or "ok", source.get("last_success_at", "never"),
            len(source.get("inventory", {}))))
    print("Retained %d change events; checked at %s" % (len(state["events"]), state["checked_at"]))
