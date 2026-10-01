"""idle.py: picking a backend, and never letting a broken one reach the timer."""
import sys
import unittest
from unittest import mock

import support  # noqa: F401  (puts the app on sys.path)
import idle


class _Broken:
    def idle_seconds(self):
        raise OSError("compositor went away")


class _Fixed:
    def idle_seconds(self):
        return 42.0


class IdleTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.multiple(idle, _backend=None, _chosen=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_first_backend_that_answers_wins(self):
        with mock.patch.object(idle, "sys", mock.Mock(platform="linux")), \
                mock.patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland-0"}), \
                mock.patch.multiple(idle, _WaylandIdle=_Broken, _MutterIdle=_Fixed):
            self.assertEqual(idle.idle_seconds(), 42.0)

    def test_nothing_works(self):
        with mock.patch.object(idle, "_pick_backend", return_value=None):
            self.assertIsNone(idle.idle_seconds())
            self.assertFalse(idle.available())

    def test_a_backend_that_breaks_later_means_unknown(self):
        with mock.patch.object(idle, "_pick_backend", return_value=_Broken()):
            self.assertIsNone(idle.idle_seconds())
        self.assertIsNone(idle._backend)

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_windows_reports_a_number(self):
        self.assertIsInstance(idle._WindowsIdle().idle_seconds(), float)


if __name__ == "__main__":
    unittest.main()
