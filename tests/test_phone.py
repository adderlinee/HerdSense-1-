import time
import unittest
from unittest import mock

import phone


class GpsReaderTest(unittest.TestCase):
    def test_latest_returns_fix_and_age(self):
        g = phone.GpsReader(5)
        self.assertEqual(g.latest(40), (None, None))
        g.fix, g.fix_time = {"lat": 1.0, "lon": 2.0, "accuracy_m": 9}, time.time() - 12
        fix, age = g.latest(40)
        self.assertEqual(fix["lat"], 1.0)
        self.assertAlmostEqual(age, 12, delta=1)

    def test_too_old_fix_is_not_sent(self):
        g = phone.GpsReader(5)
        g.fix, g.fix_time = {"lat": 1.0, "lon": 2.0, "accuracy_m": 9}, time.time() - 100
        fix, age = g.latest(40)
        self.assertIsNone(fix)
        self.assertGreater(age, 90)

    def test_read_gps_falls_back_to_network(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append((cmd[2], kw["timeout"]))
            if cmd[2] == "gps":
                raise phone.subprocess.TimeoutExpired(cmd, kw["timeout"])
            return mock.Mock(stdout='{"latitude": -6.2, "longitude": 106.8, "accuracy": 25}')

        with mock.patch.object(phone.subprocess, "run", fake_run):
            fix = phone.read_gps()
        self.assertEqual(fix["accuracy_m"], 25)
        self.assertEqual([c[0] for c in calls], ["gps", "network"])
        self.assertLessEqual(sum(c[1] for c in calls), 20)  # was up to 40 s


if __name__ == "__main__":
    unittest.main()
