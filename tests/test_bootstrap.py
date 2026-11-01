"""First-start loading of the listings (TASAHEL_SEED_CSV). subprocess is faked, nothing is really imported."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from db import get_connection  # noqa: E402
from test_base import IsolatedDatabaseTestCase  # noqa: E402


class FakeRun:
    def __init__(self, code=0):
        self.calls, self.code = [], code

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return mock.Mock(returncode=self.code)


class TestLoadSeedProperties(IsolatedDatabaseTestCase):

    def setUp(self):
        import api
        self.api = api
        self.csv = os.path.join(os.path.dirname(__file__), "seed_dummy.csv")
        with open(self.csv, "w") as f:
            f.write("a,b\n1,2\n")
        self.addCleanup(lambda: os.path.exists(self.csv) and os.remove(self.csv))
        conn = get_connection()
        conn.execute("DELETE FROM properties")
        conn.commit()
        conn.close()

    def run_loader(self, csv_setting, code=0):
        fake = FakeRun(code)
        env = {"TASAHEL_SEED_CSV": csv_setting}
        with mock.patch.dict(os.environ, env), mock.patch("subprocess.run", fake):
            self.api.load_seed_properties()
        return fake.calls

    def add_property(self):
        conn = get_connection()
        conn.execute("INSERT INTO properties (title, location, property_type, area_sqm, bedrooms, bathrooms, cash_price) VALUES ('x', 'New Cairo', 'apartment', 100, 2, 1, 3000000)")
        conn.commit()
        conn.close()

    def test_imports_then_removes_duplicates_on_an_empty_database(self):
        calls = self.run_loader(self.csv)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0][1].endswith("ingest_kaggle.py") and "--csv" in calls[0])
        self.assertTrue(calls[1][1].endswith("dedupe_properties.py"))
        self.assertIn("--apply", calls[1])
        self.assertIn("--no-backup", calls[1])

    def test_does_nothing_when_the_setting_is_absent(self):
        self.assertEqual(self.run_loader(""), [])

    def test_never_reimports_when_properties_already_exist(self):
        self.add_property()
        self.assertEqual(self.run_loader(self.csv), [])

    def test_missing_file_is_skipped_without_crashing(self):
        self.assertEqual(self.run_loader(os.path.join(os.path.dirname(__file__), "no_such.csv")), [])

    def test_failed_import_stops_before_dedupe_and_does_not_raise(self):
        calls = self.run_loader(self.csv, code=1)
        self.assertEqual(len(calls), 1)

    def test_relative_path_is_resolved_from_the_project_root(self):
        rel = os.path.relpath(self.csv, os.path.join(os.path.dirname(__file__), ".."))
        calls = self.run_loader(rel)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
