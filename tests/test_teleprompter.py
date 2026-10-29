"""The teleprompter's arithmetic and its scripts / settings in storage."""
import unittest

import teleprompter
from support import StorageTestCase


class TeleprompterMathTests(unittest.TestCase):
    def test_cues_are_not_read_out(self):
        self.assertEqual(teleprompter.word_count("Hi there [smile] and welcome back"), 5)
        self.assertEqual(teleprompter.word_count("[b-roll of the desk]"), 0)
        self.assertEqual(teleprompter.word_count(""), 0)

    def test_reading_time(self):
        self.assertEqual(teleprompter.read_seconds("word " * 150, 150), 60)
        self.assertEqual(teleprompter.read_seconds("", 150), 0)
        self.assertEqual(teleprompter.read_seconds("word " * 300, 10), 300, "speed is clamped to the minimum")
        self.assertEqual(teleprompter.format_duration(65.4), "1:05")
        self.assertEqual(teleprompter.format_duration(3725), "1:02:05")

    def test_scrolls_the_whole_text_in_the_reading_time(self):
        text_height, words, wpm = 3000, 450, 150
        speed = teleprompter.scroll_speed(wpm, text_height, words)
        self.assertAlmostEqual(text_height / speed, 450 * 60 / 150)
        self.assertEqual(teleprompter.scroll_speed(wpm, 3000, 0), 0)

    def test_html_escapes_and_colors_cues(self):
        html = teleprompter.script_html("Use <b> tags? [laugh]\n\nBye", "#ff0")
        self.assertIn("&lt;b&gt;", html)
        self.assertIn('<span style="color:#ff0; font-style:italic;">[laugh]</span>', html)
        self.assertEqual(html.count("<p>"), 3, "a blank line stays a gap")

    def test_settings_are_kept_in_range(self):
        s = teleprompter.clean_settings({"wpm": 9999, "font_size": 2, "margin": -5, "countdown": 99,
                                         "mirror": 1, "bogus": True})
        self.assertEqual((s["wpm"], s["font_size"], s["margin"], s["countdown"], s["mirror"]),
                         (teleprompter.WPM_MAX, teleprompter.FONT_MIN, 0, teleprompter.COUNTDOWN_MAX, True))
        self.assertNotIn("bogus", s)
        self.assertEqual(teleprompter.clean_settings(None), teleprompter.DEFAULT_SETTINGS)


class TeleprompterStorageTests(StorageTestCase):
    def test_scripts_round_trip(self):
        st = self.storage
        intro = st.add_teleprompter_script("  Intro ", "Hello")
        outro = st.add_teleprompter_script("", "Bye")
        st.set_teleprompter_script(intro, text="Hello again", title="   ")
        st.set_teleprompter_script("missing", text="nowhere")
        again = self.new_storage()
        self.assertEqual([(s["title"], s["text"]) for s in again.get_teleprompter_scripts()],
                         [("Intro", "Hello again"), ("Untitled script", "Bye")])
        self.assertEqual(again.get_current_teleprompter_script(), outro, "the newest one is shown")

    def test_removing_the_shown_script_shows_another(self):
        st = self.storage
        first = st.add_teleprompter_script("One")
        second = st.add_teleprompter_script("Two")
        st.remove_teleprompter_script(second)
        self.assertEqual(st.get_current_teleprompter_script(), first)
        st.remove_teleprompter_script(first)
        self.assertIsNone(st.get_current_teleprompter_script())

    def test_settings_persist_and_stay_in_range(self):
        self.storage.set_teleprompter_settings(wpm=170, mirror=True, font_size=1000)
        s = self.new_storage().get_teleprompter_settings()
        self.assertEqual((s["wpm"], s["mirror"], s["font_size"]), (170, True, teleprompter.FONT_MAX))
        self.assertEqual(s["countdown"], teleprompter.DEFAULT_SETTINGS["countdown"])

    def test_scripts_stay_out_of_the_sync_outbox(self):
        self.enable_sync()
        self.storage.add_teleprompter_script("Intro", "Hello")
        self.storage.set_teleprompter_settings(wpm=120)
        self.assertEqual(self.outbox_ops(), [])


if __name__ == "__main__":
    unittest.main()
