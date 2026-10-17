import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hddmon.collector import RawDevice
from hddmon.normalizer import normalize

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def raw(name: str, dev: str = "/dev/sda") -> RawDevice:
    return RawDevice(dev_node=dev, type_hint=None, json_data=load(name), smartctl_rc=0, used_type=None)


class TestNormalizerATA(unittest.TestCase):
    def test_healthy(self):
        snap = normalize(raw("ata_healthy.json"))
        self.assertEqual(snap.device.protocol, "ata")
        self.assertEqual(snap.device.serial, "WD-WCC3FANSP1TD")
        self.assertEqual(snap.device.model, "WDC WD10EZEX-08M2NA0")
        self.assertEqual(snap.device.capacity_bytes, 1000204886016)
        self.assertTrue(snap.smart_passed)
        self.assertEqual(snap.temperature, 34)
        self.assertEqual(snap.power_on_hours, 20152)
        self.assertEqual(snap.reallocated, 0)
        self.assertEqual(snap.pending, 0)
        self.assertEqual(snap.uncorrectable, 0)
        self.assertEqual(snap.reported_uncorrect, 0)
        self.assertEqual(snap.load_cycle, 35421)
        self.assertEqual(snap.power_cycle, 2150)
        self.assertIn("without error", snap.selftest_status)
        self.assertGreater(len(snap.attributes), 10)

    def test_failing(self):
        snap = normalize(raw("ata_failing.json", "/dev/sdb"))
        self.assertFalse(snap.smart_passed)
        self.assertEqual(snap.reallocated, 24)
        self.assertEqual(snap.pending, 8)
        self.assertEqual(snap.uncorrectable, 3)
        self.assertEqual(snap.reported_uncorrect, 12)
        self.assertEqual(snap.temperature, 42)
        self.assertEqual(snap.power_on_hours, 45000)
        self.assertIn("error", snap.selftest_status.lower())


class TestNormalizerSCSI(unittest.TestCase):
    def test_healthy(self):
        snap = normalize(raw("scsi_healthy.json", "/dev/sdc"))
        self.assertEqual(snap.device.protocol, "scsi")
        self.assertEqual(snap.device.serial, "MN1220F32JARWD")
        self.assertEqual(snap.grown_defects, 0)
        self.assertEqual(snap.reallocated, 0)
        self.assertEqual(snap.temperature, 36)
        self.assertEqual(snap.power_on_hours, 18406)
        self.assertEqual(snap.power_cycle, 4210)
        self.assertTrue(snap.smart_passed)


class TestNormalizerNVMe(unittest.TestCase):
    def test_healthy(self):
        snap = normalize(raw("nvme_healthy.json", "/dev/nvme0"))
        self.assertEqual(snap.device.protocol, "nvme")
        self.assertEqual(snap.temperature, 38)
        self.assertEqual(snap.power_on_hours, 8760)
        self.assertEqual(snap.uncorrectable, 0)
        self.assertEqual(snap.power_cycle, 340)
        self.assertTrue(snap.smart_passed)


if __name__ == "__main__":
    unittest.main()
