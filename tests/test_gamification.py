"""gamification.py: pure rules that supabase/schema.sql mirrors. If one of
these changes on purpose, change the schema to match (see CLAUDE.md)."""
import unittest
from datetime import date

import support  # noqa: F401  (puts the app on sys.path)
import gamification


class LevelTests(unittest.TestCase):
    def test_level_curve_is_monotonic_and_starts_at_one(self):
        self.assertEqual(gamification.level_from_xp(0)[0], 1)
        levels = [gamification.level_from_xp(xp)[0] for xp in range(0, 20000, 50)]
        self.assertEqual(levels, sorted(levels))

    def test_progress_within_level(self):
        level, into, needed = gamification.level_from_xp(gamification.xp_for_next_level(1))
        self.assertEqual((level, into), (2, 0))
        self.assertGreater(needed, 0)


class QuestTests(unittest.TestCase):
    def test_daily_quests_are_stable_for_a_day(self):
        # Restarting mid-day must not reshuffle in-progress quests.
        self.assertEqual(gamification.generate_daily_quests("2026-10-01"),
                         gamification.generate_daily_quests("2026-10-01"))

    def test_weekly_quests_are_stable_for_a_week(self):
        start = gamification.week_start_for(date(2026, 10, 1)).isoformat()
        self.assertEqual(gamification.generate_weekly_quests(start), gamification.generate_weekly_quests(start))

    def test_week_starts_on_tuesday(self):
        for day in range(1, 15):
            self.assertEqual(gamification.week_start_for(date(2026, 10, day)).weekday(), 1)


if __name__ == "__main__":
    unittest.main()
