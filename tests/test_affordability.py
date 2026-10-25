"""
Tests for Phase 4 — Advanced Installment/Affordability Intelligence.

Run:
    cd app
    python3 -m unittest tests.test_affordability -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from db import get_connection  # noqa: E402
from test_base import IsolatedDatabaseTestCase  # noqa: E402
from affordability import (  # noqa: E402
    assess_plan_affordability, compare_property_plans, get_affordability_profile,
)


def make_test_property_with_plans(plans):
    """plans: list of (years, down_payment_pct, monthly_payment, total_price)"""
    conn = get_connection()
    cur = conn.execute("""
        INSERT INTO properties (title, compound, location, property_type, area_sqm, bedrooms,
                                 bathrooms, finishing, cash_price, data_source)
        VALUES ('Test Affordability Property', 'Test Compound', 'New Cairo', 'apartment',
                150, 3, 2, 'fully-finished', 5000000, 'synthetic')
    """)
    conn.commit()
    prop_id = cur.lastrowid
    for years, down_pct, monthly, total in plans:
        conn.execute("""
            INSERT INTO installment_plans (property_id, years, down_payment_pct, monthly_payment, total_price)
            VALUES (?,?,?,?,?)
        """, (prop_id, years, down_pct, monthly, total))
    conn.commit()
    conn.close()
    return prop_id


def make_lead(**overrides):
    defaults = {"name": "Affordability Test Lead", "payment_preference": "installment",
                "max_monthly_payment": None, "max_down_payment": None, "budget_max": None}
    defaults.update(overrides)
    conn = get_connection()
    cur = conn.execute("""
        INSERT INTO leads (name, payment_preference, max_monthly_payment, max_down_payment, budget_max)
        VALUES (?,?,?,?,?)
    """, (defaults["name"], defaults["payment_preference"], defaults["max_monthly_payment"],
          defaults["max_down_payment"], defaults["budget_max"]))
    conn.commit()
    lead_id = cur.lastrowid
    conn.close()
    return lead_id


def delete_property(prop_id):
    conn = get_connection()
    conn.execute("DELETE FROM properties WHERE id = ?", (prop_id,))  # cascades to installment_plans
    conn.commit()
    conn.close()


def delete_lead(lead_id):
    conn = get_connection()
    conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()


class AffordabilityTestCase(IsolatedDatabaseTestCase):
    def setUp(self):
        self._props = []
        self._leads = []

    def tearDown(self):
        for pid in self._props:
            delete_property(pid)
        for lid in self._leads:
            delete_lead(lid)

    def new_property(self, plans):
        pid = make_test_property_with_plans(plans)
        self._props.append(pid)
        return pid

    def new_lead(self, **overrides):
        lid = make_lead(**overrides)
        self._leads.append(lid)
        return lid


class TestPlanLabeling(AffordabilityTestCase):

    def test_plan_within_both_constraints_is_green(self):
        lead = {"max_monthly_payment": 50_000, "max_down_payment": 600_000}
        plan = {"years": 7, "down_payment_pct": 10, "monthly_payment": 40_000, "total_price": 5_000_000}
        result = assess_plan_affordability(plan, lead)
        self.assertEqual(result["label"], "GREEN")

    def test_plan_slightly_over_is_yellow(self):
        lead = {"max_monthly_payment": 40_000, "max_down_payment": 600_000}
        # 5% over monthly -> within the 15% yellow band
        plan = {"years": 7, "down_payment_pct": 10, "monthly_payment": 42_000, "total_price": 5_000_000}
        result = assess_plan_affordability(plan, lead)
        self.assertEqual(result["label"], "YELLOW")

    def test_plan_far_over_is_red(self):
        lead = {"max_monthly_payment": 20_000, "max_down_payment": 200_000}
        plan = {"years": 7, "down_payment_pct": 10, "monthly_payment": 60_000, "total_price": 5_000_000}
        result = assess_plan_affordability(plan, lead)
        self.assertEqual(result["label"], "RED")

    def test_no_known_constraints_is_unknown_not_green(self):
        # Must never claim affordability when there's nothing to compare against —
        # this was the exact gap that caused a misleading example in conversation.py
        # during Phase 3 testing.
        lead = {"max_monthly_payment": None, "max_down_payment": None}
        plan = {"years": 7, "down_payment_pct": 10, "monthly_payment": 999_000, "total_price": 5_000_000}
        result = assess_plan_affordability(plan, lead)
        self.assertEqual(result["label"], "UNKNOWN")

    def test_reason_is_never_empty(self):
        for constraints in [{"max_monthly_payment": 40_000, "max_down_payment": None},
                             {"max_monthly_payment": None, "max_down_payment": 500_000},
                             {"max_monthly_payment": None, "max_down_payment": None}]:
            plan = {"years": 7, "down_payment_pct": 10, "monthly_payment": 40_000, "total_price": 5_000_000}
            result = assess_plan_affordability(plan, constraints)
            self.assertTrue(len(result["reason"]) > 5)


class TestPlanComparison(AffordabilityTestCase):

    def test_multiple_plans_labeled_independently(self):
        prop_id = self.new_property([
            (5, 10, 65_000, 5_400_000),   # too expensive monthly
            (7, 10, 47_000, 5_500_000),   # fits
            (10, 5, 33_000, 5_600_000),   # fits, cheaper
        ])
        lead_id = self.new_lead(max_monthly_payment=55_000, max_down_payment=600_000)
        result = compare_property_plans(prop_id, lead_id)
        labels = {p["years"]: p["label"] for p in result["plans"]}
        self.assertEqual(labels[5], "RED")
        self.assertEqual(labels[7], "GREEN")
        self.assertEqual(labels[10], "GREEN")

    def test_recommends_shortest_green_plan_not_just_cheapest(self):
        prop_id = self.new_property([
            (7, 10, 47_000, 5_500_000),   # fits, shorter term
            (10, 5, 33_000, 5_600_000),   # fits, cheaper monthly but longer
        ])
        lead_id = self.new_lead(max_monthly_payment=55_000, max_down_payment=600_000)
        result = compare_property_plans(prop_id, lead_id)
        self.assertEqual(result["recommended_plan_years"], 7)

    def test_never_recommends_red_plan_when_better_exists(self):
        prop_id = self.new_property([
            (5, 10, 65_000, 5_400_000),   # RED
            (10, 5, 33_000, 5_600_000),   # GREEN
        ])
        lead_id = self.new_lead(max_monthly_payment=55_000, max_down_payment=600_000)
        result = compare_property_plans(prop_id, lead_id)
        self.assertEqual(result["recommended_plan_years"], 10)

    def test_property_with_no_plans_handled_gracefully(self):
        prop_id = self.new_property([])
        lead_id = self.new_lead(max_monthly_payment=50_000)
        result = compare_property_plans(prop_id, lead_id)
        self.assertEqual(result["plans"], [])
        self.assertIn("No installment plans", result["recommended_reason"])

    def test_invalid_property_raises(self):
        lead_id = self.new_lead()
        with self.assertRaises(ValueError):
            compare_property_plans(999_999_999, lead_id)

    def test_invalid_lead_raises(self):
        prop_id = self.new_property([(7, 10, 40_000, 5_000_000)])
        with self.assertRaises(ValueError):
            compare_property_plans(prop_id, 999_999_999)

    def test_no_fabricated_prices_all_figures_from_stored_plans(self):
        prop_id = self.new_property([(7, 10, 47_000, 5_500_000)])
        lead_id = self.new_lead(max_monthly_payment=55_000, max_down_payment=600_000)
        result = compare_property_plans(prop_id, lead_id)
        conn = get_connection()
        stored = dict(conn.execute("SELECT * FROM installment_plans WHERE property_id = ?", (prop_id,)).fetchone())
        conn.close()
        self.assertEqual(result["plans"][0]["monthly_payment"], stored["monthly_payment"])
        self.assertEqual(result["plans"][0]["total_price"], stored["total_price"])


class TestAffordabilityProfile(AffordabilityTestCase):

    def test_cash_buyer_profile(self):
        lead_id = self.new_lead(payment_preference="cash", budget_max=6_000_000)
        profile = get_affordability_profile(lead_id)
        self.assertIn("Cash buyer", profile["summary"])
        self.assertIn("6,000,000", profile["summary"])
        self.assertIsNone(profile["estimated_max_total_price"])

    def test_installment_buyer_with_known_cap_gets_estimate(self):
        lead_id = self.new_lead(payment_preference="installment", max_monthly_payment=50_000)
        profile = get_affordability_profile(lead_id)
        self.assertIsNotNone(profile["estimated_max_total_price"])
        self.assertGreater(profile["estimated_max_total_price"], 0)
        self.assertIn("estimate", profile["summary"].lower())

    def test_installment_buyer_without_cap_gets_no_fabricated_estimate(self):
        lead_id = self.new_lead(payment_preference="installment", max_monthly_payment=None)
        profile = get_affordability_profile(lead_id)
        self.assertIsNone(profile["estimated_max_total_price"])

    def test_unknown_payment_preference_handled(self):
        lead_id = self.new_lead(payment_preference="unknown")
        profile = get_affordability_profile(lead_id)
        self.assertIn("not yet stated", profile["summary"])

    def test_estimate_derived_from_real_market_averages_not_hardcoded(self):
        lead_id = self.new_lead(payment_preference="installment", max_monthly_payment=50_000)
        profile = get_affordability_profile(lead_id)
        conn = get_connection()
        real_avg = conn.execute("SELECT AVG(years) as y FROM installment_plans").fetchone()["y"]
        conn.close()
        self.assertAlmostEqual(profile["based_on_market_averages"]["avg_years"], real_avg, places=1)


class TestAPIIntegration(IsolatedDatabaseTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
        import api
        cls.client = api.app.test_client()

    def test_affordability_profile_endpoint(self):
        resp = self.client.get("/api/leads/1/affordability-profile")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("summary", data)

    def test_property_affordability_endpoint(self):
        matches_resp = self.client.get("/api/leads/1/matches?top_n=1")
        prop_id = matches_resp.get_json()[0]["property_id"]
        resp = self.client.get(f"/api/leads/1/properties/{prop_id}/affordability")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("plans", data)
        self.assertIn("recommended_reason", data)

    def test_invalid_property_returns_404(self):
        resp = self.client.get("/api/leads/1/properties/999999999/affordability")
        self.assertEqual(resp.status_code, 404)

    def test_existing_endpoints_unaffected(self):
        for path in ("/api/leads", "/api/stats", "/api/pipeline", "/"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, f"{path} broke")


if __name__ == "__main__":
    unittest.main(verbosity=2)
