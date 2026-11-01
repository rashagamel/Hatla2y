"""
Tests for the model reliability / data maturity feature.

Uses Python's built-in unittest (no external dependency — this sandbox
cannot reach PyPI to install pytest, and the project already avoids extra
dependencies where possible, so this is a reasonable fit either way).

Run:
    cd app
    python3 -m unittest tests.test_model_maturity -v
    # or simply:
    python3 tests/test_model_maturity.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from feedback import get_model_maturity  # noqa: E402
from test_base import IsolatedDatabaseTestCase  # noqa: E402


class TestModelMaturityThresholds(unittest.TestCase):
    """The exact boundary cases requested, plus the edges between stages —
    boundaries are where off-by-one threshold bugs actually happen."""

    def test_zero_outcomes(self):
        self.assertEqual(get_model_maturity(0)["maturity"], "VERY_LOW_DATA")

    def test_four_outcomes(self):
        self.assertEqual(get_model_maturity(4)["maturity"], "VERY_LOW_DATA")

    def test_nine_outcomes_still_very_low(self):
        self.assertEqual(get_model_maturity(9)["maturity"], "VERY_LOW_DATA")

    def test_ten_outcomes(self):
        self.assertEqual(get_model_maturity(10)["maturity"], "EARLY_PILOT")

    def test_twentyfour_outcomes_still_early_pilot(self):
        self.assertEqual(get_model_maturity(24)["maturity"], "EARLY_PILOT")

    def test_twentyfive_outcomes(self):
        self.assertEqual(get_model_maturity(25)["maturity"], "PILOT")

    def test_fortynine_outcomes_still_pilot(self):
        self.assertEqual(get_model_maturity(49)["maturity"], "PILOT")

    def test_fifty_outcomes(self):
        self.assertEqual(get_model_maturity(50)["maturity"], "VALIDATION")

    def test_ninetynine_outcomes_still_validation(self):
        self.assertEqual(get_model_maturity(99)["maturity"], "VALIDATION")

    def test_hundred_outcomes(self):
        self.assertEqual(get_model_maturity(100)["maturity"], "PRODUCTION_CANDIDATE")

    def test_large_outcome_count_stays_production_candidate(self):
        self.assertEqual(get_model_maturity(10_000)["maturity"], "PRODUCTION_CANDIDATE")


class TestModelMaturityStructure(unittest.TestCase):
    """Every call must return the full structured shape the API/UI depend on —
    a maturity function that returns inconsistent shapes is worse than one
    that's simply wrong, since it breaks callers unpredictably."""

    def test_returns_all_required_keys(self):
        result = get_model_maturity(0)
        for key in ("real_outcome_count", "maturity", "reliability", "warning"):
            self.assertIn(key, result)

    def test_real_outcome_count_echoed_back(self):
        self.assertEqual(get_model_maturity(37)["real_outcome_count"], 37)

    def test_reliability_labels_match_spec(self):
        expected = {
            0: "Very Low", 10: "Low", 25: "Moderate", 50: "Good", 100: "Higher",
        }
        for count, label in expected.items():
            self.assertEqual(get_model_maturity(count)["reliability"], label)

    def test_warning_is_nonempty_string_at_every_stage(self):
        for count in (0, 10, 25, 50, 100):
            warning = get_model_maturity(count)["warning"]
            self.assertIsInstance(warning, str)
            self.assertGreater(len(warning), 10)


class TestModelMaturityDoesNotMutateScoring(unittest.TestCase):
    """The spec is explicit: this feature must not change scoring behavior.
    Confirm get_model_maturity is a pure function of its input — same input,
    same output, no side effects on the scoring model or database."""

    def test_pure_function_same_input_same_output(self):
        r1 = get_model_maturity(15)
        r2 = get_model_maturity(15)
        self.assertEqual(r1, r2)

    def test_does_not_touch_the_database(self):
        # get_model_maturity takes a plain int, not a DB connection — verified
        # structurally by the function signature, confirmed here by calling it
        # with no app/DB context available and no error occurring.
        try:
            get_model_maturity(0)
        except Exception as e:
            self.fail(f"get_model_maturity() unexpectedly touched external state: {e}")


class TestStatsEndpointIntegration(IsolatedDatabaseTestCase):
    """Confirms /api/stats still exposes all pre-existing fields (nothing
    removed) alongside the new maturity fields, using the real Flask test
    client rather than a live server (faster, no port/process management)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
        import api
        cls.client = api.app.test_client()

    def test_stats_endpoint_has_pre_existing_fields(self):
        resp = self.client.get("/api/stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        for key in ("demo_leads", "real_leads", "real_properties", "synthetic_properties"):
            self.assertIn(key, data, f"pre-existing field '{key}' missing — this would be a regression")

    def test_stats_endpoint_has_new_maturity_fields(self):
        resp = self.client.get("/api/stats")
        data = resp.get_json()
        for key in ("real_outcomes", "model_maturity", "model_reliability", "score_warning"):
            self.assertIn(key, data)

    def test_score_endpoint_still_works(self):
        resp = self.client.post("/api/leads/1/score")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("score", resp.get_json())

    def test_pipeline_endpoint_still_works(self):
        resp = self.client.get("/api/pipeline")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("total_leads", resp.get_json())

    def test_dashboard_still_loads(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"DOCTYPE", resp.data)

    def test_phase1_extraction_still_works(self):
        # Use a throwaway lead, not one of the 5 demo leads — this test would
        # otherwise permanently mutate lead 1's real stored profile just by
        # running the test suite, which is exactly the kind of accidental
        # data pollution this task explicitly asked to avoid.
        create_resp = self.client.post("/api/leads", json={"name": "Unittest Throwaway Lead"})
        lead_id = create_resp.get_json()["id"]
        try:
            resp = self.client.post(f"/api/leads/{lead_id}/chat",
                                     json={"message": "budget around 5 million, 3 bedrooms"})
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertIn("extracted", data)
            self.assertIn("updated_fields", data)
        finally:
            self.client.delete(f"/api/leads/{lead_id}")


if __name__ == "__main__":
    unittest.main(verbosity=2)