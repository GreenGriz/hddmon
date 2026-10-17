import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hddmon.analyzer import analyze
from hddmon.collector import RawDevice
from hddmon.config import Thresholds
from hddmon.models import Verdict
from hddmon.normalizer import normalize

FIXTURES = Path(__file__).parent / "fixtures"
TH = Thresholds()


def snap_from(name: str, dev: str = "/dev/sda"):
    data = json.loads((FIXTURES / name).read_text())
    return normalize(RawDevice(dev_node=dev, type_hint=None, json_data=data, smartctl_rc=0, used_type=None))


class FakePrev:
    def __init__(self, **kw):
        self._d = kw
        self.verdict = kw.get("verdict", "OK")

    def __getitem__(self, key):
        return self._d.get(key)


class TestAnalyzer(unittest.TestCase):
    def test_healthy_ok(self):
        snap = snap_from("ata_healthy.json")
        a = analyze(snap, None, TH)
        self.assertEqual(a.verdict, Verdict.OK)
        self.assertEqual(a.reasons, [])

    def test_failing_replace_now(self):
        snap = snap_from("ata_failing.json", "/dev/sdb")
        a = analyze(snap, None, TH)
        self.assertEqual(a.verdict, Verdict.REPLACE_NOW)
        self.assertTrue(any("FAILED" in r for r in a.reasons))

    def test_growth_backup_now(self):
        prev = FakePrev(reallocated=0, pending=0, uncorrectable=0, reported_uncorrect=0,
                        end_to_end_error=0, command_timeout=0, verdict="OK")
        snap = snap_from("ata_failing.json", "/dev/sdb")
        a = analyze(snap, prev, TH)
        self.assertEqual(a.verdict, Verdict.REPLACE_NOW)
        self.assertIn("reallocated", a.deltas)
        self.assertEqual(a.deltas["reallocated"], 24)
        growth_events = [e for e in a.events if e.type == "attr_growth"]
        self.assertTrue(growth_events)

    def test_pending_watch(self):
        snap = snap_from("ata_healthy.json")
        snap.pending = 3
        snap.uncorrectable = 0
        snap.reported_uncorrect = 0
        snap.reallocated = 0
        a = analyze(snap, None, TH)
        self.assertEqual(a.verdict, Verdict.BACKUP_SOON)

    def test_verdict_change_event(self):
        prev = FakePrev(reallocated=0, pending=0, uncorrectable=0, reported_uncorrect=0,
                        end_to_end_error=0, command_timeout=0, verdict="OK")
        snap = snap_from("ata_failing.json", "/dev/sdb")
        a = analyze(snap, prev, TH)
        changes = [e for e in a.events if e.type == "verdict_change"]
        self.assertTrue(changes)
        self.assertIn("REPLACE_NOW", changes[0].message)

    def test_temperature_watch(self):
        snap = snap_from("ata_healthy.json")
        snap.temperature = 46
        a = analyze(snap, None, TH)
        self.assertEqual(a.verdict, Verdict.WATCH)

    def test_power_on_hours_watch(self):
        snap = snap_from("ata_healthy.json")
        snap.power_on_hours = 50000
        a = analyze(snap, None, TH)
        self.assertEqual(a.verdict, Verdict.WATCH)


if __name__ == "__main__":
    unittest.main()
