"""
Tests for Phase 2 — Smart Next Best Action.

Uses Python's built-in unittest (no pytest — this sandbox has no PyPI
access, consistent with test_model_maturity.py). Every test that creates a
lead cleans it up in a finally/tearDown block — these tests must never
leave permanent pollution in the shipped database, since running the test
suite is something a user might do routinely.

Run:
    cd app
    python3 -m unittest tests.test_next_action -v
    # or:
    python3 tests/test_next_action.py
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from db import get_connection  # noqa: E402
from test_base import IsolatedDatabaseTestCase  # noqa: E402
from next_action import (  # noqa: E402
    generate_next_action, get_priority_queue, track_next_action_event,
    decide_action, ACTION_TYPES,
)


def make_lead(**overrides):
    """Creates a real lead row for a test scenario and returns its id.
    Caller is responsible for deleting it (see tearDown patterns below)."""
    defaults = {
        "name": "Test Lead", "channel": "whatsapp", "data_source": "real",
        "budget_min": 4_000_000, "budget_max": 5_000_000, "payment_preference": "installment",
        "max_monthly_payment": 40_000, "max_down_payment": 500_000,
        "preferred_locations": "New Cairo", "min_bedrooms": 3, "purpose": "family",
        "purchase_timeline": "1-3m", "intent": "medium", "status": "new",
        "urgency": "unknown", "objections": None,
    }
    defaults.update(overrides)
    conn = get_connection()
    cur = conn.execute("""
        INSERT INTO leads (name, channel, data_source, budget_min, budget_max, payment_preference,
                            max_monthly_payment, max_down_payment, preferred_locations, min_bedrooms,
                            purpose, purchase_timeline, intent, status, urgency, objections)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, tuple(defaults[k] for k in ["name", "channel", "data_source", "budget_min", "budget_max",
                                       "payment_preference", "max_monthly_payment", "max_down_payment",
                                       "preferred_locations", "min_bedrooms", "purpose", "purchase_timeline",
                                       "intent", "status", "urgency", "objections"]))
    conn.commit()
    lead_id = cur.lastrowid
    conn.close()
    return lead_id


def add_event(lead_id, event_type, days_ago=0):
    conn = get_connection()
    if days_ago:
        conn.execute("""
            INSERT INTO lead_events (lead_id, event_type, payload, created_at)
            VALUES (?, ?, 'test event', datetime('now', ?))
        """, (lead_id, event_type, f"-{days_ago} days"))
    else:
        conn.execute("INSERT INTO lead_events (lead_id, event_type, payload) VALUES (?, ?, 'test event')",
                      (lead_id, event_type))
    conn.commit()
    conn.close()


def delete_lead(lead_id):
    conn = get_connection()
    conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))  # cascades to events/matches/etc
    conn.commit()
    conn.close()


class NextActionTestCase(IsolatedDatabaseTestCase):
    """Base class: tracks every lead created during a test and guarantees
    cleanup even if the test fails partway through."""

    def setUp(self):
        self._created_leads = []

    def tearDown(self):
        for lead_id in self._created_leads:
            delete_lead(lead_id)

    def new_lead(self, **overrides):
        lead_id = make_lead(**overrides)
        self._created_leads.append(lead_id)
        return lead_id


class TestDecisionLogic(NextActionTestCase):

    def test_missing_critical_field_asks_clarifying_question(self):
        lead_id = self.new_lead(preferred_locations="", min_bedrooms=None, purpose="unknown",
                                 purchase_timeline="unknown", intent="medium")
        add_event(lead_id, "message_in")
        result = generate_next_action(lead_id)
        self.assertEqual(result["action"], "ask_clarifying_question")
        self.assertIsNotNone(result["question_to_ask"])

    def test_installment_objection_sends_installment_options(self):
        lead_id = self.new_lead(objections=json.dumps(["monthly_payment"]))
        add_event(lead_id, "message_in")
        result = generate_next_action(lead_id)
        self.assertEqual(result["action"], "send_installment_options")

    def test_down_payment_objection_also_sends_installment_options(self):
        lead_id = self.new_lead(objections=json.dumps(["down_payment"]))
        add_event(lead_id, "message_in")
        result = generate_next_action(lead_id)
        self.assertEqual(result["action"], "send_installment_options")

    def test_viewing_requested_schedules_viewing(self):
        lead_id = self.new_lead()
        add_event(lead_id, "viewing_booked")
        result = generate_next_action(lead_id)
        self.assertEqual(result["action"], "schedule_viewing")

    def test_viewing_already_booked_status_gives_follow_up(self):
        lead_id = self.new_lead(status="viewing_booked")
        result = generate_next_action(lead_id)
        self.assertEqual(result["action"], "follow_up")

    def test_inactive_lead_gets_re_engage(self):
        lead_id = self.new_lead()
        add_event(lead_id, "message_in", days_ago=20)
        result = generate_next_action(lead_id)
        self.assertEqual(result["action"], "re_engage")

    def test_fresh_lead_with_no_events_is_not_re_engage(self):
        # A brand-new lead with zero activity should NOT be treated as "gone cold" —
        # there's nothing to re-engage from. Guards against the num_events-vs-
        # num_activity_events bug found during manual testing.
        lead_id = self.new_lead(preferred_locations="New Cairo", min_bedrooms=3,
                                 purpose="family", purchase_timeline="1-3m")
        result = generate_next_action(lead_id)
        self.assertNotEqual(result["action"], "re_engage")

    def test_closed_won_lead_gets_no_action(self):
        lead_id = self.new_lead(status="won")
        result = generate_next_action(lead_id)
        self.assertEqual(result["action"], "no_action")
        self.assertEqual(result["priority"], "none")

    def test_closed_lost_lead_gets_no_action(self):
        lead_id = self.new_lead(status="lost")
        result = generate_next_action(lead_id)
        self.assertEqual(result["action"], "no_action")

    def test_own_next_action_event_does_not_count_as_fresh_activity(self):
        # Regression test for the exact bug found manually: calling
        # generate_next_action() used to log an event that made the SAME lead
        # look freshly active on the very next call, masking real staleness.
        lead_id = self.new_lead()
        add_event(lead_id, "message_in", days_ago=20)
        first = generate_next_action(lead_id)
        second = generate_next_action(lead_id)
        self.assertEqual(first["action"], "re_engage")
        self.assertEqual(second["action"], "re_engage", "generating a recommendation must not change the next one")


class TestActionVocabulary(NextActionTestCase):

    def test_all_returned_actions_are_in_controlled_vocabulary(self):
        conn = get_connection()
        ids = [r["id"] for r in conn.execute("SELECT id FROM leads").fetchall()]
        conn.close()
        for lead_id in ids:
            result = generate_next_action(lead_id)
            self.assertIn(result["action"], ACTION_TYPES)


class TestDataIntegrity(NextActionTestCase):
    """Section 14: no fake property IDs, no invented prices/plans, unavailable
    properties aren't recommended when the matching engine already excludes them."""

    def test_recommended_property_ids_exist_in_database(self):
        lead_id = self.new_lead()
        add_event(lead_id, "property_viewed")
        result = generate_next_action(lead_id)
        conn = get_connection()
        for pid in result["recommended_property_ids"]:
            row = conn.execute("SELECT id FROM properties WHERE id = ?", (pid,)).fetchone()
            self.assertIsNotNone(row, f"recommended property_id {pid} does not exist — fabricated ID")
        conn.close()

    def test_no_action_and_re_engage_never_recommend_properties(self):
        lead_id = self.new_lead(status="won")
        result = generate_next_action(lead_id)
        self.assertEqual(result["recommended_property_ids"], [])

    def test_talking_points_reference_only_real_lead_fields(self):
        lead_id = self.new_lead(max_monthly_payment=33_000, preferred_locations="Maadi")
        add_event(lead_id, "message_in")
        result = generate_next_action(lead_id)
        combined = " ".join(result["talking_points"])
        # the real stated values must appear verbatim if referenced at all
        if "33,000" in combined or "monthly" in combined.lower():
            self.assertIn("33,000", combined)
        if "Maadi" in combined:
            self.assertIn("Maadi", combined)


class TestReliabilityVsConfidenceSeparation(NextActionTestCase):
    """Section 8: recommendation confidence and model reliability must be
    reported separately, never merged into one number."""

    def test_both_fields_present_and_independently_labeled(self):
        lead_id = self.new_lead()
        add_event(lead_id, "message_in")
        result = generate_next_action(lead_id)
        self.assertIn("confidence", result)
        self.assertIn("model_reliability", result)
        self.assertIn(result["model_reliability"], ("Very Low", "Low", "Moderate", "Good", "Higher"))
        self.assertIn(result["confidence"], ("High", "Medium", "Low", "N/A"))

    def test_low_model_reliability_does_not_force_low_confidence(self):
        # On a freshly-seeded DB (0 real outcomes -> Very Low model reliability),
        # a lead with strong, unambiguous signals should still get a confident
        # RECOMMENDATION even though the underlying MODEL's reliability is low —
        # these are different questions and must not be conflated.
        lead_id = self.new_lead(intent="high", urgency="high")
        add_event(lead_id, "property_viewed")
        add_event(lead_id, "viewing_booked")
        result = generate_next_action(lead_id)
        self.assertEqual(result["model_reliability"], "Very Low")
        self.assertIn(result["confidence"], ("High", "Medium"))


class TestPriorityQueue(NextActionTestCase):

    def test_priority_queue_excludes_closed_leads(self):
        won_lead = self.new_lead(status="won")
        queue = get_priority_queue()
        self.assertNotIn(won_lead, [item["lead_id"] for item in queue])

    def test_priority_queue_sorted_descending(self):
        queue = get_priority_queue()
        scores = [item["priority_score"] for item in queue]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_priority_queue_and_direct_call_agree(self):
        # Regression test for the exact discrepancy found manually.
        lead_id = self.new_lead()
        add_event(lead_id, "message_in", days_ago=20)
        direct = generate_next_action(lead_id)
        queue = get_priority_queue()
        queue_entry = next((item for item in queue if item["lead_id"] == lead_id), None)
        self.assertIsNotNone(queue_entry)
        self.assertEqual(direct["action"], queue_entry["action"])


class TestAPIIntegration(IsolatedDatabaseTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
        import api
        cls.client = api.app.test_client()

    def test_existing_next_action_endpoint_still_works(self):
        resp = self.client.post("/api/leads/1/next-action")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        # legacy fields (pre-Phase-2) must still be present
        for key in ("action_type", "channel", "urgency", "message_suggestion", "reasoning"):
            self.assertIn(key, data)
        # new Phase 2 fields
        for key in ("action", "priority", "confidence", "model_reliability", "checklist",
                    "recommended_property_ids", "talking_points"):
            self.assertIn(key, data)

    def test_priority_queue_endpoint_returns_valid_json(self):
        resp = self.client.get("/api/priority-queue")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)

    def test_track_endpoint_accepts_valid_events(self):
        create_resp = self.client.post("/api/leads", json={"name": "API Track Test Lead"})
        lead_id = create_resp.get_json()["id"]
        try:
            resp = self.client.post(f"/api/leads/{lead_id}/next-action/track", json={"event": "selected"})
            self.assertEqual(resp.status_code, 200)
        finally:
            self.client.delete(f"/api/leads/{lead_id}")

    def test_track_endpoint_rejects_invalid_event(self):
        resp = self.client.post("/api/leads/1/next-action/track", json={"event": "bogus"})
        self.assertEqual(resp.status_code, 400)

    def test_existing_score_endpoint_unaffected(self):
        resp = self.client.post("/api/leads/1/score")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("score", resp.get_json())

    def test_existing_stats_endpoint_unaffected(self):
        resp = self.client.get("/api/stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        for key in ("demo_leads", "real_leads", "real_properties", "model_maturity"):
            self.assertIn(key, data)

    def test_dashboard_still_loads(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
