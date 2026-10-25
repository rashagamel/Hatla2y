"""
Test Database Isolation Harness.

Provides temporary, fully-isolated SQLite databases for test suites.
Guarantees:
  1. Tests never touch or mutate the operational database (data/processed/copilot.db)
  2. Tests run against clean, deterministic schemas and fixtures
  3. No test failures due to external data states
  4. Automatic cleanup of temporary database files on teardown
"""
import os
import sys
import tempfile
import unittest
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from db import set_db_path, reset_db_path, init_db, get_connection


def seed_minimal_test_fixtures(conn):
    """Seeds a controlled set of properties and leads for test isolation."""
    # 1. Properties
    test_properties = [
        ("Apartment in New Cairo", "Mivida", "New Cairo", "apartment", 150, 3, 2, "fully-finished", 1, 0, 1, 1, "Emaar", 6_000_000, 2026),
        ("Villa in New Cairo", "Katameya Heights", "New Cairo", "villa", 350, 4, 4, "luxury", 1, 1, 1, 1, "Emaar", 18_000_000, 2025),
        ("Apartment in Sheikh Zayed", "Allegria", "Sheikh Zayed", "apartment", 160, 3, 2, "fully-finished", 0, 0, 1, 1, "SODIC", 6_500_000, 2026),
        ("Townhouse in 6th of October", "Palm Hills", "6th of October", "townhouse", 240, 3, 3, "semi-finished", 0, 1, 1, 1, "Palm Hills", 7_500_000, 2027),
        ("Chalet in North Coast", "Marassi", "North Coast", "chalet", 120, 2, 2, "fully-finished", 1, 0, 1, 1, "Emaar", 8_000_000, 2025),
        ("Apartment in Mostakbal City", "Bloomfields", "Mostakbal City", "apartment", 140, 3, 2, "core", 0, 0, 1, 1, "Tatweer", 4_500_000, 2028),
        ("Duplex in New Capital", "IL Bosco", "New Capital", "duplex", 220, 3, 3, "fully-finished", 0, 0, 1, 1, "Misr Italia", 6_200_000, 2026),
        ("Apartment in El Shorouk", "El Patio 5", "El Shorouk", "apartment", 130, 2, 2, "semi-finished", 0, 0, 1, 1, "La Vista", 3_500_000, 2026),
    ]

    for title, comp, loc, ptype, area, bed, bath, finish, pool, garden, park, sec, dev, price, deliv in test_properties:
        cur = conn.execute("""
            INSERT INTO properties
            (title, compound, location, property_type, area_sqm, bedrooms, bathrooms,
             finishing, has_pool, has_garden, has_parking, has_security,
             developer, cash_price, delivery_year, description, data_source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (title, comp, loc, ptype, area, bed, bath, finish, pool, garden, park, sec, dev, price, deliv,
              f"{title} in {comp}, {loc}. Real market-calibrated test property.", "synthetic"))
        prop_id = cur.lastrowid

        # Add 7-year and 5-year installment plans
        conn.execute("""
            INSERT INTO installment_plans (property_id, years, down_payment_pct, monthly_payment, total_price, plan_source)
            VALUES (?, 7, 10.0, ?, ?, 'assumed_default')
        """, (prop_id, round((price * 0.9) / 84, -2), price))
        conn.execute("""
            INSERT INTO installment_plans (property_id, years, down_payment_pct, monthly_payment, total_price, plan_source)
            VALUES (?, 5, 15.0, ?, ?, 'assumed_default')
        """, (prop_id, round((price * 0.85) / 60, -2), price * 1.05))

    # 2. Leads with activity history
    now = datetime.datetime.now()
    t0 = now - datetime.timedelta(days=2)
    cur = conn.execute("""
        INSERT INTO leads (id, name, phone, channel, data_source, budget_min, budget_max, payment_preference,
                            max_monthly_payment, max_down_payment, preferred_locations,
                            min_bedrooms, purpose, purchase_timeline, intent, status, created_at, updated_at)
        VALUES (1, 'Ahmed Hassan', '+201011111111', 'whatsapp', 'synthetic',
                4500000, 6000000, 'installment', 55000, 600000, 'New Cairo,Sheikh Zayed',
                3, 'family', '1-3m', 'high', 'new', ?, ?)
    """, (t0.isoformat(sep=" ", timespec="seconds"), t0.isoformat(sep=" ", timespec="seconds")))

    conn.execute("""
        INSERT INTO lead_events (lead_id, event_type, payload, created_at)
        VALUES (1, 'message_in', 'I want a 3-bedroom in New Cairo or Sheikh Zayed', ?)
    """, (t0.isoformat(sep=" ", timespec="seconds"),))
    conn.execute("""
        INSERT INTO lead_events (lead_id, event_type, payload, created_at)
        VALUES (1, 'message_out', 'Options sent to lead', ?)
    """, ((t0 + datetime.timedelta(hours=1)).isoformat(sep=" ", timespec="seconds"),))
    conn.execute("""
        INSERT INTO lead_events (lead_id, event_type, payload, created_at)
        VALUES (1, 'property_viewed', 'Viewed property #1', ?)
    """, ((t0 + datetime.timedelta(hours=4)).isoformat(sep=" ", timespec="seconds"),))
    conn.execute("""
        INSERT INTO lead_events (lead_id, event_type, payload, created_at)
        VALUES (1, 'viewing_booked', 'Site visit booked', ?)
    """, ((t0 + datetime.timedelta(hours=8)).isoformat(sep=" ", timespec="seconds"),))

    conn.commit()


class IsolatedDatabaseTestCase(unittest.TestCase):
    """
    Base test case that creates an isolated temporary database for each test class.
    Ensures zero cross-contamination and zero leakage into production copilot.db.
    """
    _temp_db_file = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Create temp file
        temp_fd, temp_path = tempfile.mkstemp(prefix="copilot_test_", suffix=".db")
        os.close(temp_fd)
        cls._temp_db_file = temp_path

        # Direct db module to temp path
        set_db_path(temp_path)
        os.environ["COPILOT_DB_PATH"] = temp_path

        # Initialize schema and seed fixtures
        init_db(reset=True, db_path=temp_path)
        conn = get_connection(temp_path)
        seed_minimal_test_fixtures(conn)
        conn.close()

    @classmethod
    def tearDownClass(cls):
        # Reset DB path
        reset_db_path()
        os.environ.pop("COPILOT_DB_PATH", None)

        if cls._temp_db_file and os.path.exists(cls._temp_db_file):
            try:
                os.remove(cls._temp_db_file)
            except Exception:
                pass
        super().tearDownClass()
