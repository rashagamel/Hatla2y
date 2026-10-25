"""
Tests for Phase 3 — AI Sales Conversation Copilot.

Uses Python's built-in unittest, consistent with test_model_maturity.py and
test_next_action.py. Every lead created is cleaned up in tearDown.

Run:
    cd app
    python3 -m unittest tests.test_conversation -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from db import get_connection  # noqa: E402
from test_base import IsolatedDatabaseTestCase  # noqa: E402
from extraction import extract_profile  # noqa: E402
from conversation import generate_conversation_reply, build_conversation_reply  # noqa: E402


def make_lead(**overrides):
    defaults = {"name": "Test Lead", "preferred_locations": "", "min_bedrooms": None,
                "payment_preference": "unknown", "max_monthly_payment": None}
    defaults.update(overrides)
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO leads (name, preferred_locations, min_bedrooms, payment_preference, max_monthly_payment) "
        "VALUES (?,?,?,?,?)",
        (defaults["name"], defaults["preferred_locations"], defaults["min_bedrooms"],
         defaults["payment_preference"], defaults["max_monthly_payment"]),
    )
    conn.commit()
    lead_id = cur.lastrowid
    conn.close()
    return lead_id


def delete_lead(lead_id):
    conn = get_connection()
    conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()


class ConversationTestCase(IsolatedDatabaseTestCase):
    def setUp(self):
        self._created = []

    def tearDown(self):
        for lead_id in self._created:
            delete_lead(lead_id)

    def new_lead(self, **overrides):
        lead_id = make_lead(**overrides)
        self._created.append(lead_id)
        return lead_id


class TestNegativePreferenceExtraction(unittest.TestCase):
    """The doc's own worked example, and variations on it."""

    def test_exact_spec_example(self):
        msg = "The monthly payment is too high and I don't really like Sheikh Zayed."
        result = extract_profile(msg)
        self.assertIn("monthly_payment", result["objections"])
        self.assertIn("Sheikh Zayed", result["removed_locations"])
        self.assertEqual(result["engagement_signal"], "still_interested")

    def test_adverb_between_negation_and_verb_still_detected(self):
        # Regression test for the exact bug found: "don't REALLY like" wasn't
        # matched by a literal "don't like" substring check.
        for phrase in ["don't really like Maadi", "don't particularly like Maadi",
                        "not really interested in Maadi", "no longer interested in Maadi"]:
            result = extract_profile(f"I {phrase}")
            self.assertIn("Maadi", result["removed_locations"], f"failed on: {phrase}")

    def test_unrelated_negation_does_not_falsely_remove_location(self):
        # "I don't like small kitchens, New Cairo works great" — the negation
        # and the location aren't in the same clause, so New Cairo must NOT
        # be removed just because a negation cue appears somewhere earlier.
        result = extract_profile("I don't like small kitchens. New Cairo works great for us.")
        self.assertNotIn("New Cairo", result["removed_locations"])

    def test_losing_interest_signal_detected(self):
        result = extract_profile("Please cancel, I'm not interested anymore.")
        self.assertEqual(result["engagement_signal"], "losing_interest")

    def test_no_signal_when_nothing_notable_said(self):
        result = extract_profile("Hi, just checking in.")
        self.assertIsNone(result["engagement_signal"])


class TestMergeRemovesLocations(ConversationTestCase):

    def test_removal_wins_over_existing_preference(self):
        lead_id = self.new_lead(preferred_locations="New Cairo,Sheikh Zayed")
        generate_conversation_reply(lead_id, "I don't really like Sheikh Zayed anymore.")
        conn = get_connection()
        lead = conn.execute("SELECT preferred_locations FROM leads WHERE id=?", (lead_id,)).fetchone()
        conn.close()
        locs = lead["preferred_locations"].split(",")
        self.assertNotIn("Sheikh Zayed", locs)
        self.assertIn("New Cairo", locs)

    def test_removal_wins_even_when_same_message_also_positively_mentions_it(self):
        # "I don't like Sheikh Zayed" ALSO gets picked up by the positive location
        # matcher (it contains the substring "Sheikh Zayed") — removal must win,
        # not silently re-add what was just ruled out.
        lead_id = self.new_lead(preferred_locations="")
        generate_conversation_reply(lead_id, "I don't like Sheikh Zayed.")
        conn = get_connection()
        lead = conn.execute("SELECT preferred_locations FROM leads WHERE id=?", (lead_id,)).fetchone()
        conn.close()
        self.assertNotIn("Sheikh Zayed", (lead["preferred_locations"] or "").split(","))


class TestSuggestedReply(ConversationTestCase):

    def test_reply_generated_for_spec_example(self):
        lead_id = self.new_lead(preferred_locations="Sheikh Zayed", min_bedrooms=3,
                                 payment_preference="installment", max_monthly_payment=40_000)
        result = generate_conversation_reply(
            lead_id, "The monthly payment is too high and I don't really like Sheikh Zayed.")
        self.assertIsNotNone(result["suggested_reply"])
        # Sheikh Zayed legitimately appears in the reply as the location being moved AWAY
        # from ("alternatives outside Sheikh Zayed") — that's correct, not a bug. What
        # actually matters is verified separately in TestMergeRemovesLocations: it's no
        # longer stored as a live preference.
        self.assertIn("lower monthly payment", result["suggested_reply"].lower())

    def test_no_reply_when_nothing_notable_said(self):
        lead_id = self.new_lead()
        result = generate_conversation_reply(lead_id, "Hi, just checking in on availability.")
        self.assertIsNone(result["suggested_reply"])

    def test_reply_offers_only_real_locations_from_actual_matches(self):
        lead_id = self.new_lead(preferred_locations="Sheikh Zayed", min_bedrooms=3)
        result = generate_conversation_reply(lead_id, "I don't like Sheikh Zayed anymore.")
        if result["suggested_reply"] and "like" in result["suggested_reply"]:
            # any location named in the reply must be a location that appears in a real match
            match_locations = {m["location"] for m in result["matches_preview"]}
            for loc in ("New Cairo", "6th of October", "North Coast", "Nasr City", "Maadi",
                        "Zamalek", "New Capital", "Madinaty", "Mostakbal City", "El Shorouk",
                        "Ain Sokhna", "Red Sea", "Heliopolis"):
                if loc in result["suggested_reply"]:
                    self.assertIn(loc, match_locations, f"'{loc}' mentioned in reply but not in any real match")

    def test_does_not_cite_mismatched_example_without_known_cap(self):
        # Regression test for the exact bug found: citing a specific monthly-payment
        # example as reassurance when the lead has no stated cap at all — matching.py
        # trivially marks everything as "fitting" when there's no constraint to violate.
        lead_id = self.new_lead(preferred_locations="Sheikh Zayed", min_bedrooms=3,
                                 max_monthly_payment=None)
        result = generate_conversation_reply(
            lead_id, "The monthly payment is too high and I don't really like Sheikh Zayed.")
        self.assertNotIn("For example", result["suggested_reply"])

    def test_cites_example_when_real_cap_and_real_fit_exist(self):
        # min_bedrooms=2: the fixture's only unit with a plan under 40k/month is a 2-bedroom
        # (El Shorouk). With min_bedrooms=3 there is genuinely no fit, and the reply must not
        # cite one (see TestReplyUsesOnlyQualifiedMatches).
        lead_id = self.new_lead(preferred_locations="Sheikh Zayed", min_bedrooms=2,
                                 payment_preference="installment", max_monthly_payment=40_000)
        result = generate_conversation_reply(
            lead_id, "The monthly payment is too high and I don't really like Sheikh Zayed.")
        self.assertIn("For example", result["suggested_reply"])


class TestReplyUsesOnlyQualifiedMatches(unittest.TestCase):
    """build_conversation_reply is a pure function: hand-built matches, no database."""

    @staticmethod
    def match(title, location, hard_fail=(), plan_fit=None):
        reasons = [plan_fit] if plan_fit else []
        return {"title": title, "location": location, "hard_fail": list(hard_fail), "reasons": reasons,
                "warnings": []}

    def test_does_not_cite_a_unit_that_fails_the_leads_requirements(self):
        lead = {"name": "Dina Samir", "preferred_locations": "", "max_monthly_payment": 40_000}
        extracted = {"objections": ["monthly_payment"], "removed_locations": ["Sheikh Zayed"]}
        matches = [self.match("Apartment in El Shorouk", "El Shorouk", hard_fail=["bedrooms"],
                              plan_fit="7-year plan fits: 37,500 EGP/month, 350,000 EGP down payment")]
        reply = build_conversation_reply(lead, extracted, matches)
        self.assertNotIn("For example", reply)
        self.assertNotIn("El Shorouk", reply)      # not offered as an alternative either

    def test_does_not_offer_failing_locations_as_alternatives(self):
        lead = {"name": "Dina", "preferred_locations": "", "max_monthly_payment": 40_000}
        extracted = {"objections": [], "removed_locations": ["Sheikh Zayed"]}
        matches = [self.match("A", "Mostakbal City", hard_fail=["payment_plan"]),
                   self.match("B", "New Cairo")]
        reply = build_conversation_reply(lead, extracted, matches)
        self.assertNotIn("Mostakbal City", reply)
        self.assertIn("New Cairo", reply)

    def test_says_so_honestly_when_nothing_qualifies(self):
        lead = {"name": "Dina", "preferred_locations": "", "max_monthly_payment": 40_000}
        extracted = {"objections": [], "removed_locations": ["Sheikh Zayed"]}
        matches = [self.match("A", "Mostakbal City", hard_fail=["payment_plan"])]
        reply = build_conversation_reply(lead, extracted, matches)
        self.assertIn("Let me see what else is available", reply)
        self.assertNotIn("within your budget", reply)

    def test_still_cites_a_qualified_unit_with_a_real_fit(self):
        lead = {"name": "Dina", "preferred_locations": "", "max_monthly_payment": 40_000}
        extracted = {"objections": ["monthly_payment"], "removed_locations": []}
        matches = [self.match("Apartment in El Shorouk", "El Shorouk",
                              plan_fit="7-year plan fits: 37,500 EGP/month, 350,000 EGP down payment")]
        reply = build_conversation_reply(lead, extracted, matches)
        self.assertIn("For example, Apartment in El Shorouk", reply)


class TestIntentSignalLogging(ConversationTestCase):

    def test_engagement_signal_logged_as_lead_event(self):
        lead_id = self.new_lead(preferred_locations="Sheikh Zayed")
        generate_conversation_reply(lead_id, "I don't like Sheikh Zayed anymore.")
        conn = get_connection()
        events = conn.execute(
            "SELECT * FROM lead_events WHERE lead_id = ? AND event_type = 'intent_signal'", (lead_id,)
        ).fetchall()
        conn.close()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"], "still_interested")

    def test_no_intent_signal_event_when_nothing_notable(self):
        lead_id = self.new_lead()
        generate_conversation_reply(lead_id, "Hi, just checking in.")
        conn = get_connection()
        events = conn.execute(
            "SELECT * FROM lead_events WHERE lead_id = ? AND event_type = 'intent_signal'", (lead_id,)
        ).fetchall()
        conn.close()
        self.assertEqual(len(events), 0)


class TestAPIIntegration(IsolatedDatabaseTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
        import api
        cls.client = api.app.test_client()

    def test_chat_endpoint_returns_phase3_fields(self):
        create_resp = self.client.post("/api/leads", json={"name": "API Copilot Test",
                                                              "preferred_locations": "Sheikh Zayed"})
        lead_id = create_resp.get_json()["id"]
        try:
            resp = self.client.post(f"/api/leads/{lead_id}/chat",
                                     json={"message": "I don't really like Sheikh Zayed anymore."})
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            for key in ("extracted", "updated_fields", "removed_preferences",
                        "engagement_signal", "suggested_reply"):
                self.assertIn(key, data)
            self.assertIn("Sheikh Zayed", data["removed_preferences"]["locations"])
        finally:
            self.client.delete(f"/api/leads/{lead_id}")

    def test_existing_next_action_endpoint_unaffected(self):
        resp = self.client.post("/api/leads/1/next-action")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("action", resp.get_json())

    def test_existing_stats_endpoint_unaffected(self):
        resp = self.client.get("/api/stats")
        self.assertEqual(resp.status_code, 200)

    def test_dashboard_still_loads(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)