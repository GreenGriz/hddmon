import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hddmon import cli
from hddmon.collector import RawDevice

FIXTURES = Path(__file__).parent / "fixtures"


DEVICES = [
    ("/dev/sda", "ata_healthy.json"),
    ("/dev/sdb", "ata_failing.json"),
    ("/dev/sdc", "scsi_healthy.json"),
    ("/dev/nvme0", "nvme_healthy.json"),
]


def make_raws(devices=DEVICES):
    raws = []
    for dev, name in devices:
        data = json.loads((FIXTURES / name).read_text())
        raws.append(RawDevice(dev_node=dev, type_hint=None, json_data=data, smartctl_rc=0, used_type=None))
    return raws


def fake_collect_all(cfg):
    return make_raws(), [], [dev for dev, _ in DEVICES]


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, collect_impl=fake_collect_all):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("hddmon.collector.collect_all", collect_impl), \
             mock.patch("hddmon.notify.notify_events", lambda *a, **k: []), \
             redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["--db", self.db, *args])
        return code, out.getvalue(), err.getvalue()

    def event_types(self):
        with sqlite3.connect(self.db) as conn:
            return [r[0] for r in conn.execute("SELECT type FROM events")]

    def test_collect_then_status(self):
        code, out, err = self.run_cli("collect")
        self.assertEqual(code, 40)  # worst = REPLACE_NOW (sdb)
        self.assertIn("/dev/sdb", out)
        self.assertIn("REPLACE_NOW", out)

        code, out, _ = self.run_cli("status", "--json")
        self.assertEqual(code, 40)
        data = json.loads(out)
        self.assertEqual(len(data), 4)
        by_dev = {d["dev_node"]: d for d in data}
        self.assertEqual(by_dev["/dev/sda"]["verdict"], "OK")
        self.assertEqual(by_dev["/dev/sdb"]["verdict"], "REPLACE_NOW")
        self.assertEqual(by_dev["/dev/sda"]["serial"], "WD-WCC3FANSP1TD")

    def test_collect_is_idempotent_on_serial(self):
        self.run_cli("collect")
        code, out, _ = self.run_cli("devices", "--json")
        data = json.loads(out)
        self.assertEqual(len(data), 4)
        self.assertTrue(all(d["present"] for d in data))

    def test_history(self):
        self.run_cli("collect")
        code, out, _ = self.run_cli("history", "WD-WCC3FANSP1TD")
        self.assertEqual(code, 0)
        self.assertIn("WD-WCC3FANSP1TD", out)

    def test_smart(self):
        self.run_cli("collect")
        code, out, _ = self.run_cli("smart", "WD-WCC3FANSP1TD")
        self.assertEqual(code, 0)
        self.assertIn("/dev/sda", out)
        self.assertIn("Reallocated_Sector_Ct", out)
        self.assertIn("SMART: passed", out)

        code, out, _ = self.run_cli("smart", "WD-WCC3FANSP1TD", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["device"]["serial"], "WD-WCC3FANSP1TD")
        self.assertEqual(data["snapshot"]["verdict"], "OK")
        by_id = {a["attr_id"]: a for a in data["attributes"] if a["attr_id"] is not None}
        self.assertEqual(by_id[5]["name"], "Reallocated_Sector_Ct")

    def test_smart_scsi_attributes(self):
        self.run_cli("collect")
        code, out, _ = self.run_cli("smart", "MN1220F32JARWD")
        self.assertEqual(code, 0)
        self.assertIn("grown_defects", out)

    def test_smart_unknown_device(self):
        self.run_cli("collect")
        err = io.StringIO()
        with redirect_stderr(err):
            code, _, _ = self.run_cli("smart", "NOPE")
        self.assertEqual(code, 1)

    def test_report_json(self):
        self.run_cli("collect")
        code, out, _ = self.run_cli("report", "--json")
        self.assertEqual(code, 40)
        data = json.loads(out)
        self.assertEqual(len(data["devices"]), 4)

    def test_predict(self):
        self.run_cli("collect")
        code, out, _ = self.run_cli("predict", "--json")
        data = json.loads(out)
        self.assertEqual(len(data), 4)

    def test_predict_message(self):
        self.run_cli("collect")
        _, out, _ = self.run_cli("predict")
        self.assertIn("мало снимков для прогноза", out)
        self.run_cli("collect")
        self.run_cli("collect")
        _, out, _ = self.run_cli("predict")
        self.assertIn("нет роста критичных атрибутов", out)

    def test_unknown_device_history(self):
        self.run_cli("collect")
        err = io.StringIO()
        with redirect_stderr(err):
            code, _, _ = self.run_cli("history", "NOPE")
        self.assertEqual(code, 1)

    def test_collect_partial_failure_keeps_device_present(self):
        self.run_cli("collect")
        raws = make_raws(DEVICES[:3])
        errors = ["/dev/nvme0: all device types failed (timeout)"]
        discovered = [dev for dev, _ in DEVICES]
        _, _, err = self.run_cli(
            "collect", collect_impl=lambda cfg: (raws, errors, discovered),
        )
        self.assertIn("ERROR: /dev/nvme0", err)
        _, out, _ = self.run_cli("devices", "--json")
        self.assertTrue(all(d["present"] for d in json.loads(out)))
        self.assertNotIn("device_missing", self.event_types())

    def test_collect_disappeared_device_marks_missing_once(self):
        self.run_cli("collect")
        raws = make_raws([DEVICES[0], DEVICES[2], DEVICES[3]])
        discovered = ["/dev/sda", "/dev/sdc", "/dev/nvme0"]
        self.run_cli("collect", collect_impl=lambda cfg: (raws, [], discovered))
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 40)
        self.assertIn("[MISSING]", out)
        self.assertEqual(self.event_types().count("device_missing"), 1)

    def test_replaced_disk_marks_old_one_missing(self):
        self.run_cli("collect")
        raws = make_raws()
        old_serial = raws[1].json_data["serial_number"]
        raws[1].json_data = {**raws[1].json_data, "serial_number": "NEWSERIAL"}
        discovered = [dev for dev, _ in DEVICES]
        self.run_cli("collect", collect_impl=lambda cfg: (raws, [], discovered))
        _, out, _ = self.run_cli("devices", "--json")
        by_serial = {d["serial"]: d for d in json.loads(out)}
        self.assertEqual(by_serial[old_serial]["present"], 0)
        self.assertEqual(by_serial["NEWSERIAL"]["present"], 1)
        self.assertEqual(self.event_types().count("device_missing"), 1)

    def test_device_new_event_only_once(self):
        self.run_cli("collect")
        self.assertEqual(self.event_types().count("device_new"), 4)
        self.run_cli("collect")
        self.assertEqual(self.event_types().count("device_new"), 4)

    def test_view_commands_work_with_readonly_db(self):
        self.run_cli("collect")
        db = Path(self.db)
        db.chmod(0o444)
        Path(self.tmp.name).chmod(0o555)
        try:
            code, out, _ = self.run_cli("status")
            self.assertEqual(code, 40)
            self.assertIn("/dev/sdb", out)
            code, out, _ = self.run_cli("report", "--json")
            self.assertEqual(code, 40)
            self.assertEqual(len(json.loads(out)["devices"]), 4)
            code, out, _ = self.run_cli("smart", "WD-WCC3FANSP1TD")
            self.assertEqual(code, 0)
            self.assertIn("Reallocated_Sector_Ct", out)
        finally:
            Path(self.tmp.name).chmod(0o755)
            db.chmod(0o644)

    def test_fmt_time_converts_to_local(self):
        from datetime import datetime
        value = "2026-01-01T00:00:00+00:00"
        expected = datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M %Z")
        self.assertEqual(cli._fmt_time(value), expected)


if __name__ == "__main__":
    unittest.main()
