import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hddmon.models import DeviceInfo, Snapshot
from hddmon import store


def make_snap(dev="/dev/sda", serial="SER1", t=None, **kw):
    now = t or datetime.now(timezone.utc)
    base = dict(temperature=30, power_on_hours=1000, reallocated=0, pending=0,
                uncorrectable=0, reported_uncorrect=0)
    base.update(kw)
    return Snapshot(
        device=DeviceInfo(dev_node=dev, model="TEST", serial=serial, protocol="ata"),
        taken_at=now,
        smart_passed=True,
        **base,
    )


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.connect(str(Path(self.tmp.name) / "t.db"))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_upsert_and_history(self):
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        s0 = make_snap(t=t0)
        did = store.upsert_device(self.conn, s0)
        store.insert_snapshot(self.conn, did, s0, "OK", 0, [], False)
        s1 = make_snap(t=t0 + timedelta(days=1), reallocated=5)
        did2 = store.upsert_device(self.conn, s1)
        self.assertEqual(did, did2)
        store.insert_snapshot(self.conn, did2, s1, "BACKUP_SOON", 20, ["reallocated=5"], False)
        self.conn.commit()
        hist = store.get_history(self.conn, did)
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0]["reallocated"], 5)  # сортировка DESC

    def test_mark_missing(self):
        s = make_snap()
        did = store.upsert_device(self.conn, s)
        store.insert_snapshot(self.conn, did, s, "OK", 0, [], False)
        missing = store.mark_missing(self.conn, [])
        self.assertEqual(len(missing), 1)
        devs = store.list_devices(self.conn)
        self.assertEqual(devs[0]["present"], 0)

    def test_critical_series(self):
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        did = store.upsert_device(self.conn, make_snap(t=t0))
        for i, v in enumerate([0, 2, 5]):
            s = make_snap(t=t0 + timedelta(days=i), reallocated=v)
            store.insert_snapshot(self.conn, did, s, "OK", 0, [], False)
        series = store.critical_series(self.conn, did, "reallocated")
        self.assertEqual([v for _, v in series], [0, 2, 5])

    def test_critical_series_rejects_unknown(self):
        with self.assertRaises(ValueError):
            store.critical_series(self.conn, 1, "model")


if __name__ == "__main__":
    unittest.main()
