"""tests/parity/cases.json must match what the Python rules say today; the
SQL (tests/sql) and phone (tests/pwa) tests check their copies of the rules
against the same file."""
import os
import sys
import unittest

import support  # noqa: F401  (puts the app on sys.path)

sys.path.insert(0, os.path.join(support.ROOT, "tools"))
import parity_cases  # noqa: E402


class ParityCasesTests(unittest.TestCase):
    def test_cases_file_is_up_to_date(self):
        with open(parity_cases.CASES_FILE, encoding="utf-8") as f:
            on_disk = f.read()
        self.assertTrue(on_disk == parity_cases.render(),
                        "a mirrored rule changed: run `python tools/parity_cases.py`, then make "
                        "supabase/schema.sql and docs/app.js pass tests/sql and tests/pwa again")


if __name__ == "__main__":
    unittest.main()
