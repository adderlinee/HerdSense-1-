import unittest

from rules import LOST, SEPARATED, TOGETHER, WAITING, Config, Member, ema, evaluate, haversine_m

BASE = (-6.1754, 106.8272)
M_PER_DEG_LON = 111320 * 0.99443  # cos(6.1754 deg)


def mem(name, east_m=0.0, now=100.0, rms=1.5, acc=8.0, age=0.0):
    return Member(name, BASE[0], BASE[1] + east_m / M_PER_DEG_LON, acc, rms, now - age)


class RulesTest(unittest.TestCase):
    cfg = Config(separation_m=100, grace_s=60)

    def run_eval(self, members, now=100.0, memory=None):
        memory = {} if memory is None else memory
        return evaluate({m.name: m for m in members}, now, self.cfg, memory), memory

    def test_haversine(self):
        self.assertAlmostEqual(haversine_m(0, 0, 0, 1), 111195, delta=100)
        self.assertEqual(haversine_m(1, 1, 1, 1), 0)

    def test_together(self):
        r, _ = self.run_eval([mem("a"), mem("b", 30), mem("c", 50)])
        self.assertTrue(all(v["status"] == TOGETHER for v in r.values()))

    def test_far_and_moving_is_separated(self):
        r, _ = self.run_eval([mem("a"), mem("b"), mem("c", 300)])
        self.assertEqual(r["c"]["status"], SEPARATED)
        self.assertEqual(r["a"]["status"], TOGETHER)

    def test_two_people_far_apart_both_flagged(self):
        r, _ = self.run_eval([mem("a"), mem("b", 300)])
        self.assertEqual(r["a"]["status"], SEPARATED)
        self.assertEqual(r["b"]["nearest"], "a")

    def test_far_and_still_waits_then_escalates(self):
        mm = {}
        r, mm = self.run_eval([mem("a"), mem("b"), mem("c", 300, rms=0.1)], now=100, memory=mm)
        self.assertEqual(r["c"]["status"], WAITING)
        r, mm = self.run_eval([mem("a", now=170), mem("b", now=170), mem("c", 300, now=170, rms=0.1)],
                              now=170, memory=mm)
        self.assertEqual(r["c"]["status"], SEPARATED)

    def test_stale_or_bad_gps_is_lost(self):
        r, _ = self.run_eval([mem("a"), mem("b"), mem("c", age=40)])
        self.assertEqual(r["c"]["status"], LOST)
        r, _ = self.run_eval([mem("a"), mem("b"), mem("c", acc=200)])
        self.assertEqual(r["c"]["status"], LOST)

    def test_hysteresis(self):
        mm = {}
        _, mm = self.run_eval([mem("a"), mem("b", 130)], memory=mm)   # separated
        r, mm = self.run_eval([mem("a"), mem("b", 90)], memory=mm)    # inside 100 but not inside 80
        self.assertEqual(r["b"]["status"], SEPARATED)
        r, mm = self.run_eval([mem("a"), mem("b", 70)], memory=mm)
        self.assertEqual(r["b"]["status"], TOGETHER)

    # ---- group of 4-5 people: the case "nearest groupmate" could not catch ----
    def test_two_left_behind_are_both_separated(self):
        """5 people: 3 in front, 2 behind. Old nearest-groupmate logic called the 2 behind TOGETHER."""
        r, _ = self.run_eval([mem("a", 0), mem("b", 20), mem("c", 40), mem("d", 300), mem("e", 310)])
        self.assertEqual(r["d"]["status"], SEPARATED)
        self.assertEqual(r["e"]["status"], SEPARATED)
        for n in "abc":
            self.assertEqual(r[n]["status"], TOGETHER)
        self.assertEqual(r["d"]["group_size"], 2)

    def test_two_left_behind_and_still_wait_first(self):
        r, _ = self.run_eval([mem("a"), mem("b", 20), mem("c", 40), mem("d", 300, rms=0.1), mem("e", 310, rms=0.1)])
        self.assertEqual(r["d"]["status"], WAITING)
        self.assertEqual(r["e"]["status"], WAITING)

    def test_split_two_vs_two_without_leader_flags_everyone(self):
        r, _ = self.run_eval([mem("a", 0), mem("b", 20), mem("c", 300), mem("d", 320)])
        self.assertTrue(all(v["status"] == SEPARATED for v in r.values()))

    def test_leader_cluster_wins_a_tie(self):
        cfg = Config(separation_m=100, grace_s=60, leader="a")
        ms = [mem("a", 0), mem("b", 20), mem("c", 300), mem("d", 320)]
        r = evaluate({m.name: m for m in ms}, 100.0, cfg, {})
        self.assertEqual(r["a"]["status"], TOGETHER)
        self.assertEqual(r["b"]["status"], TOGETHER)
        self.assertEqual(r["c"]["status"], SEPARATED)
        self.assertEqual(r["d"]["status"], SEPARATED)

    def test_chain_of_people_stays_one_group(self):
        r, _ = self.run_eval([mem("a", 0), mem("b", 80), mem("c", 160), mem("d", 240)])
        self.assertTrue(all(v["status"] == TOGETHER for v in r.values()))

    def test_lost_reason(self):
        r, _ = self.run_eval([mem("a"), mem("b"), mem("c", age=60), mem("d", acc=200)])
        self.assertEqual(r["c"]["reason"], "stale")
        self.assertEqual(r["d"]["reason"], "poor_gps")

    def test_lost_member_does_not_break_the_group(self):
        r, _ = self.run_eval([mem("a"), mem("b", 20), mem("c", 500, age=60)])
        self.assertEqual(r["a"]["status"], TOGETHER)
        self.assertEqual(r["c"]["status"], LOST)

    def test_single_usable_member_is_together(self):
        r, _ = self.run_eval([mem("a"), mem("b", age=60)])
        self.assertEqual(r["a"]["status"], TOGETHER)

    def test_ema_smooths_a_jump(self):
        self.assertEqual(ema(None, 10.0, 0.5), 10.0)
        self.assertAlmostEqual(ema(10.0, 20.0, 0.5), 15.0)
        self.assertEqual(ema(10.0, 20.0, 1.0), 20.0)  # alpha 1 = off


if __name__ == "__main__":
    unittest.main()
