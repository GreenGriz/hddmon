import io
import json
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


def fake_collect_all(cfg):
    devices = [
        ("/dev/sda", "ata_healthy.json"),
        ("/dev/sdb", "ata_failing.json"),
        ("/dev/sdc", "scsi_healthy.json"),
        ("/dev/nvme0", "nvme_healthy.json"),
    ]
    raws = []
    for dev, name in devices:
        data = json.loads((FIXTURES / name).read_text())
        raws.append(RawDevice(dev_node=dev, type_hint=None, json_data=data, smartctl_rc=0, used_type=None))
    return raws, []


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("hddmon.collector.collect_all", fake_collect_all), \
             mock.patch("hddmon.notify.notify_events", lambda *a, **k: []), \
             redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["--db", self.db, *args])
        return code, out.getvalue(), err.getvalue()

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

    def test_unknown_device_history(self):
        self.run_cli("collect")
        err = io.StringIO()
        with redirect_stderr(err):
            code, _, _ = self.run_cli("history", "NOPE")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
