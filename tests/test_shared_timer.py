"""The one-timer-across-devices rules (shared_timer.py)."""
import unittest

import support  # noqa: F401 - puts the app on sys.path
import shared_timer

PHASES = ("work", "short_break", "long_break")


class SharedTimerTests(unittest.TestCase):
    def test_session_key(self):
        self.assertEqual(shared_timer.session_key("work", 1000.4), "work:1000")
        self.assertIsNone(shared_timer.session_key("work", None))

    def test_only_the_owner_counts_a_finished_phase(self):
        self.assertTrue(shared_timer.counts_here(True, owner=True, sharing=True))
        self.assertFalse(shared_timer.counts_here(True, owner=False, sharing=True))
        self.assertTrue(shared_timer.counts_here(True, owner=False, sharing=False), "not shared: always here")
        self.assertTrue(shared_timer.counts_here(False, owner=False, sharing=True), "a skip publishes")

    def test_next_phase_is_chained_onto_the_last(self):
        self.assertEqual(shared_timer.chained_end(1000, 300, now=1001), 1300)
        self.assertIsNone(shared_timer.chained_end(1000, 300, now=1300), "already over: start from now")
        self.assertIsNone(shared_timer.chained_end(None, 300, now=1000))

    def test_what_is_worth_following(self):
        now = 10_000
        self.assertTrue(shared_timer.followable({"phase": "work", "running": True, "ends_at": now + 60}, PHASES, now))
        self.assertTrue(shared_timer.followable({"phase": "work", "running": True, "ends_at": now - 30}, PHASES, now))
        self.assertFalse(shared_timer.followable({"phase": "work", "running": True, "ends_at": now - 61}, PHASES, now))
        self.assertTrue(shared_timer.followable({"phase": "work", "running": False, "seconds_left": 60}, PHASES, now))
        self.assertFalse(shared_timer.followable({"phase": "nap"}, PHASES, now))
        self.assertFalse(shared_timer.followable(None, PHASES, now))

    def test_the_phone_waits_as_long(self):
        import re
        with open(f"{support.ROOT}/docs/app.js", encoding="utf-8") as f:
            ms = int(re.search(r"const TIMER_TAKEOVER_MS = (\d+);", f.read()).group(1))
        self.assertEqual(ms, shared_timer.TAKEOVER_SECONDS * 1000)
