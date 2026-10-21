"""opencode CLI adapter: event parsing and base stability, with a fake CLI."""
import importlib.util
import json
import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

_spec = importlib.util.spec_from_file_location(
    "opencode_measure", os.path.join(os.path.dirname(__file__), "..", "scripts", "opencode_measure.py"))
om = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(om)


def fake_run(counts):
    """counts(text) -> input tokens; emits opencode JSON events."""
    def run(cmd, **kwargs):
        text = cmd[-1]
        events = [{"type": "step_start"},
                  {"type": "step_finish", "part": {"tokens": {"input": counts(text) - 5, "output": 1,
                                                              "cache": {"read": 5, "write": 0}}}}]
        return SimpleNamespace(stdout="\n".join(json.dumps(e) for e in events), stderr="", returncode=0)
    return run


class OpencodeMeasureTests(unittest.TestCase):
    def test_counts_include_cache_and_cancel_fixed_overhead(self):
        probes = [("a", "xy"), ("b", "xyz")]
        with mock.patch.object(om.subprocess, "run", fake_run(lambda t: 700 + len(t))):
            row = om.measure("p/m", "base ", probes, "cfg", None)
        self.assertTrue(row["complete"])
        self.assertEqual(row["base_api"], 705)
        self.assertEqual(row["deltas"], {"a": 2, "b": 3})

    def test_unstable_base_marks_row_incomplete(self):
        calls = []

        def counts(text):
            calls.append(text)
            return 700 + len(text) + (1 if len(calls) > 2 and text == "base " else 0)

        with mock.patch.object(om.subprocess, "run", fake_run(counts)):
            row = om.measure("p/m", "base ", [("a", "x")], "cfg", None)
        self.assertFalse(row["complete"])
        self.assertNotEqual(row["base_api"], row["base_api_after"])

    def test_error_event_is_reported(self):
        def run(cmd, **kwargs):
            return SimpleNamespace(stdout=json.dumps({"type": "error", "error": {"name": "ModelNotFound"}}),
                                   stderr="", returncode=1)

        with mock.patch.object(om.subprocess, "run", run):
            row = om.measure("p/m", "base ", [("a", "x")], "cfg", None, retries=0)
        self.assertEqual(row["class"], "error")
        self.assertIn("ModelNotFound", row["error_detail"])

    def test_timeout_is_an_error(self):
        def run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 1)

        with mock.patch.object(om.subprocess, "run", run):
            self.assertEqual(om.prompt_tokens("p/m", "t", "cfg", "."), (None, "timeout"))


if __name__ == "__main__":
    unittest.main()
