"""
Test Database Isolation Verification.

Verifies:
  1. Tests operate on an isolated temporary database
  2. Modifying rows during tests does not alter or pollute data/processed/copilot.db
  3. Dynamic db.DB_PATH correctly redirects to the temporary database during tests
"""
import os
import sys
import unittest
import sqlite3

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from db import get_connection, get_db_path, DEFAULT_DB_PATH
from test_base import IsolatedDatabaseTestCase


class TestDatabaseIsolation(IsolatedDatabaseTestCase):

    def test_active_db_is_temporary(self):
        active_path = get_db_path()
        self.assertNotEqual(active_path, DEFAULT_DB_PATH)
        self.assertTrue(active_path.endswith(".db"))
        self.assertIn("copilot_test_", active_path)

    def test_isolation_from_operational_database(self):
        conn = get_connection()
        # Insert a canary lead into the test database
        cur = conn.execute("INSERT INTO leads (name, phone) VALUES ('Canary Isolation Lead', '01000000000')")
        conn.commit()
        canary_id = cur.lastrowid
        conn.close()

        # Verify canary exists in test database
        conn_test = get_connection()
        test_row = conn_test.execute("SELECT id FROM leads WHERE id = ?", (canary_id,)).fetchone()
        conn_test.close()
        self.assertIsNotNone(test_row)

        # Verify canary does NOT exist in the operational database
        if os.path.exists(DEFAULT_DB_PATH):
            conn_prod = sqlite3.connect(DEFAULT_DB_PATH)
            has_leads = conn_prod.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='leads'").fetchone()
            prod_row = None
            if has_leads:   # an empty or fresh file has no leads table, so nothing could have leaked
                prod_row = conn_prod.execute("SELECT id FROM leads WHERE name = 'Canary Isolation Lead'").fetchone()
            conn_prod.close()
            self.assertIsNone(prod_row, "Canary test lead must never leak into operational copilot.db!")


if __name__ == "__main__":
    unittest.main()