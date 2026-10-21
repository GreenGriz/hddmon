import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hddmon.config import Thresholds
from hddmon import guide


class TestGuide(unittest.TestCase):
    def test_ata_has_core_parameters(self):
        items = guide.critical_help("ata")
        keys = {h.key for h in items}
        for key in ("reallocated", "pending", "uncorrectable", "reported_uncorrect",
                    "end_to_end_error", "command_timeout", "temperature",
                    "power_on_hours", "load_cycle", "overall_health", "selftest"):
            self.assertIn(key, keys)
        self.assertEqual([h.attr_id for h in items if h.key == "pending"], [197])

    def test_scsi_and_nvme_specific_parameters(self):
        scsi_keys = {h.key for h in guide.critical_help("scsi")}
        self.assertIn("grown_defects", scsi_keys)
        self.assertNotIn("pending", scsi_keys)
        nvme_keys = {h.key for h in guide.critical_help("nvme")}
        self.assertIn("media_errors", nvme_keys)
        self.assertIn("percentage_used", nvme_keys)

    def test_unknown_protocol_falls_back_to_ata(self):
        self.assertEqual(
            [h.key for h in guide.critical_help(None)],
            [h.key for h in guide.critical_help("ata")],
        )

    def test_thresholds_are_substituted(self):
        items = guide.critical_help("ata", Thresholds(reallocated_backup_now=42,
                                                      pending_backup_now=7,
                                                      temp_critical=61))
        by_key = {h.key: h for h in items}
        self.assertIn("42", by_key["reallocated"].worry)
        self.assertIn("7", by_key["pending"].worry)
        self.assertIn("61", by_key["temperature"].worry)

    def test_each_item_describes_all_four_aspects(self):
        for h in guide.critical_help("ata") + guide.critical_help("scsi") + guide.critical_help("nvme"):
            for field in ("what", "shows", "why", "worry"):
                self.assertTrue(getattr(h, field).strip(), f"{h.key}.{field} пуст")


if __name__ == "__main__":
    unittest.main()
