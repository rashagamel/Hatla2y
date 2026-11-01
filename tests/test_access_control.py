"""
Access-control tests for app/api.py.

They replace api.require_auth with a fake that maps "Bearer <name>" to a user, so they test the
rules in api.py (who may call what, which leads an agent can see, webhook signature, login throttle)
independently of how auth.py issues tokens.

Run: py -m pytest tests/test_access_control.py -q
"""
import hashlib
import hmac
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from db import get_connection  # noqa: E402
from test_base import IsolatedDatabaseTestCase  # noqa: E402

APP_SECRET = "test-app-secret"
VERIFY_TOKEN = "test-verify-token"


class AccessControlTestCase(IsolatedDatabaseTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import api
        cls.api = api
        cls.client = api.app.test_client()

    def setUp(self):
        from agents import create_agent
        self.a1 = create_agent(name="Agent One")
        self.a2 = create_agent(name="Agent Two")
        self.lead_a1 = self.make_lead("Lead of agent 1", self.a1)
        self.lead_a2 = self.make_lead("Lead of agent 2", self.a2)
        self.lead_free = self.make_lead("Unassigned lead", None)
        self.users = {
            "manager": {"id": 1, "email": "boss@t.com", "name": "Boss", "role": "manager", "agent_id": None},
            "agent1": {"id": 2, "email": "a1@t.com", "name": "A1", "role": "agent", "agent_id": self.a1},
            "agent2": {"id": 3, "email": "a2@t.com", "name": "A2", "role": "agent", "agent_id": self.a2},
        }
        api = self.api

        def fake_require_auth(req):
            token = req.headers.get("Authorization", "").replace("Bearer ", "")
            user = self.users.get(token)
            if user is None:
                return None, (api.jsonify({"error": "unauthorized"}), 401)
            return dict(user), None

        for p in (mock.patch.object(api, "require_auth", fake_require_auth),
                  mock.patch.dict(os.environ, {"TASAHEL_REQUIRE_AUTH": "1",
                                               "WHATSAPP_APP_SECRET": APP_SECRET,
                                               "WHATSAPP_VERIFY_TOKEN": VERIFY_TOKEN})):
            p.start()
            self.addCleanup(p.stop)
        api._LOGIN_FAILURES.clear()

    def tearDown(self):
        conn = get_connection()
        for lid in (self.lead_a1, self.lead_a2, self.lead_free):
            conn.execute("DELETE FROM leads WHERE id = ?", (lid,))
        conn.commit()
        conn.close()

    @staticmethod
    def make_lead(name, agent_id):
        conn = get_connection()
        cur = conn.execute("INSERT INTO leads (name, assigned_agent_id) VALUES (?, ?)", (name, agent_id))
        conn.commit()
        lid = cur.lastrowid
        conn.close()
        return lid

    def call(self, method, path, who=None, **kw):
        headers = kw.pop("headers", {})
        if who:
            headers["Authorization"] = f"Bearer {who}"
        return getattr(self.client, method)(path, headers=headers, **kw)


class TestLoginRequired(AccessControlTestCase):

    def test_data_routes_reject_anonymous_requests(self):
        for path in ("/api/leads", "/api/properties", "/api/stats", "/api/priority-queue",
                     f"/api/leads/{self.lead_a1}", "/api/agents", "/api/pipeline",
                     "/api/analytics/manager"):
            self.assertEqual(self.call("get", path).status_code, 401, path)

    def test_a_bad_token_is_rejected(self):
        self.assertEqual(self.call("get", "/api/leads", who="nobody").status_code, 401)

    def test_login_endpoint_stays_public(self):
        resp = self.call("post", "/api/auth/login", json={})
        self.assertEqual(resp.status_code, 400)        # reaches our validation, not the login guard

    def test_pages_stay_public_so_the_login_screen_can_load(self):
        self.assertEqual(self.call("get", "/login").status_code, 200)


class TestGuestDemoModeStillWorksLocally(AccessControlTestCase):

    def test_without_the_flag_the_dashboard_works_as_guest(self):
        with mock.patch.dict(os.environ, {"TASAHEL_REQUIRE_AUTH": "0"}):
            self.assertEqual(self.call("get", "/api/leads").status_code, 200)
            self.assertEqual(self.call("get", "/api/stats").status_code, 200)

    def test_an_invalid_token_is_still_rejected_in_demo_mode(self):
        with mock.patch.dict(os.environ, {"TASAHEL_REQUIRE_AUTH": "0"}):
            self.assertEqual(self.call("get", "/api/leads", who="nobody").status_code, 401)


class TestAgentSeesOnlyTheirLeads(AccessControlTestCase):

    def names(self, who):
        return {l["name"] for l in self.call("get", "/api/leads", who=who).get_json()}

    def test_agent_list_is_own_plus_unassigned(self):
        seen = self.names("agent1")
        self.assertIn("Lead of agent 1", seen)          # own lead
        self.assertIn("Unassigned lead", seen)          # open pool (other unassigned leads may exist too)
        self.assertNotIn("Lead of agent 2", seen)       # another agent's lead is hidden

    def test_manager_sees_everything(self):
        self.assertTrue({"Lead of agent 1", "Lead of agent 2", "Unassigned lead"} <= self.names("manager"))

    def test_agent_cannot_open_another_agents_lead(self):
        for suffix in ("", "/matches", "/intelligence", "/pipeline-history", "/assignments"):
            resp = self.call("get", f"/api/leads/{self.lead_a2}{suffix}", who="agent1")
            self.assertEqual(resp.status_code, 404, suffix)

    def test_agent_cannot_write_to_another_agents_lead(self):
        self.assertEqual(self.call("put", f"/api/leads/{self.lead_a2}", who="agent1",
                                   json={"name": "hijacked"}).status_code, 404)
        self.assertEqual(self.call("post", f"/api/leads/{self.lead_a2}/chat", who="agent1",
                                   json={"message": "hello"}).status_code, 404)
        self.assertEqual(self.call("put", f"/api/leads/{self.lead_a2}/status", who="agent1",
                                   json={"status": "contacted"}).status_code, 404)

    def test_agent_can_open_own_and_unassigned_leads(self):
        for lid in (self.lead_a1, self.lead_free):
            self.assertEqual(self.call("get", f"/api/leads/{lid}", who="agent1").status_code, 200)

    def test_manager_can_open_any_lead(self):
        self.assertEqual(self.call("get", f"/api/leads/{self.lead_a2}", who="manager").status_code, 200)

    def test_priority_queue_is_filtered_for_agents(self):
        ids = {q["lead_id"] for q in self.call("get", "/api/priority-queue", who="agent1").get_json()}
        self.assertNotIn(self.lead_a2, ids)

    def test_lead_created_by_an_agent_is_assigned_to_them(self):
        resp = self.call("post", "/api/leads", who="agent1", json={"name": "New by agent"})
        self.assertEqual(resp.status_code, 201)
        lid = resp.get_json()["id"]
        try:
            conn = get_connection()
            owner = conn.execute("SELECT assigned_agent_id FROM leads WHERE id = ?", (lid,)).fetchone()
            conn.close()
            self.assertEqual(owner["assigned_agent_id"], self.a1)
        finally:
            self.call("delete", f"/api/leads/{lid}", who="manager")


class TestManagerOnlyActions(AccessControlTestCase):

    def test_agents_are_refused(self):
        refused = [
            ("delete", f"/api/leads/{self.lead_a1}", None),
            ("post", f"/api/leads/{self.lead_a1}/assign", {"agent_id": self.a2}),
            ("post", "/api/retrain", {}),
            ("get", "/api/pipeline", None),
            ("get", "/api/analytics/manager", None),
            ("get", "/api/analytics/funnel", None),
            ("get", "/api/analytics/agents", None),
            ("get", "/api/data-quality", None),
            ("post", "/api/agents", {"name": "x"}),
            ("post", f"/api/agents/{self.a1}/deactivate", {}),
            ("get", "/api/users", None),
        ]
        for method, path, body in refused:
            kw = {"json": body} if body is not None else {}
            self.assertEqual(self.call(method, path, who="agent1", **kw).status_code, 403, f"{method} {path}")

    def test_the_lead_is_still_there_after_an_agent_tried_to_delete_it(self):
        self.call("delete", f"/api/leads/{self.lead_a1}", who="agent1")
        self.assertEqual(self.call("get", f"/api/leads/{self.lead_a1}", who="manager").status_code, 200)

    def test_manager_is_not_blocked(self):
        for method, path in (("get", "/api/pipeline"), ("get", "/api/analytics/manager"),
                             ("get", "/api/data-quality")):
            self.assertNotIn(self.call(method, path, who="manager").status_code, (401, 403), path)


class TestWhatsAppWebhook(AccessControlTestCase):

    STATUS_ONLY = {"entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}]}

    def signed(self, body, secret=APP_SECRET):
        raw = json.dumps(body).encode()
        sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        return {"data": raw, "headers": {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}}

    def test_unsigned_post_is_rejected(self):
        resp = self.call("post", "/api/whatsapp/webhook", json=self.STATUS_ONLY)
        self.assertEqual(resp.status_code, 403)

    def test_wrong_signature_is_rejected(self):
        resp = self.call("post", "/api/whatsapp/webhook", **self.signed(self.STATUS_ONLY, secret="wrong"))
        self.assertEqual(resp.status_code, 403)

    def test_unsigned_post_creates_no_lead(self):
        body = {"entry": [{"changes": [{"value": {"messages": [
            {"from": "201999999999", "text": {"body": "hi"}}]}}]}]}
        self.call("post", "/api/whatsapp/webhook", json=body)
        conn = get_connection()
        n = conn.execute("SELECT COUNT(*) c FROM leads WHERE phone = '201999999999'").fetchone()["c"]
        conn.close()
        self.assertEqual(n, 0)

    def test_signed_status_update_is_acknowledged_not_retried(self):
        resp = self.call("post", "/api/whatsapp/webhook", **self.signed(self.STATUS_ONLY))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "ignored")

    def test_signed_non_text_message_is_acknowledged(self):
        body = {"entry": [{"changes": [{"value": {"messages": [{"from": "201", "type": "image"}]}}]}]}
        self.assertEqual(self.call("post", "/api/whatsapp/webhook", **self.signed(body)).status_code, 200)

    def test_garbage_payload_is_still_a_400(self):
        self.assertEqual(self.call("post", "/api/whatsapp/webhook", **self.signed({})).status_code, 400)

    def test_verification_handshake(self):
        ok = self.call("get", f"/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token={VERIFY_TOKEN}"
                              "&hub.challenge=12345")
        self.assertEqual((ok.status_code, ok.get_data(as_text=True)), (200, "12345"))
        bad = self.call("get", "/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=1")
        self.assertEqual(bad.status_code, 403)

    def test_handshake_refuses_when_no_verify_token_is_configured(self):
        with mock.patch.dict(os.environ, {"WHATSAPP_VERIFY_TOKEN": ""}):
            resp = self.call("get", "/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=&hub.challenge=1")
            self.assertEqual(resp.status_code, 403)


class TestLoginThrottle(AccessControlTestCase):

    def test_repeated_failures_lock_that_email_temporarily(self):
        def bad_login(email, password):
            raise ValueError("invalid credentials")
        with mock.patch.object(self.api, "authenticate_user", bad_login):
            codes = [self.call("post", "/api/auth/login",
                               json={"email": "victim@t.com", "password": "x"}).status_code
                     for _ in range(12)]
            self.assertEqual(codes[:10], [401] * 10)
            self.assertEqual(codes[10:], [429, 429])
            other = self.call("post", "/api/auth/login", json={"email": "someone@else.com", "password": "x"})
            self.assertEqual(other.status_code, 401)      # a different email is not locked

    def test_a_successful_login_clears_the_counter(self):
        calls = {"n": 0}

        def flaky(email, password):
            calls["n"] += 1
            if password != "right":
                raise ValueError("invalid credentials")
            return {"token": "t"}
        with mock.patch.object(self.api, "authenticate_user", flaky):
            for _ in range(9):
                self.call("post", "/api/auth/login", json={"email": "u@t.com", "password": "bad"})
            self.assertEqual(self.call("post", "/api/auth/login",
                                       json={"email": "u@t.com", "password": "right"}).status_code, 200)
            for _ in range(9):
                self.assertEqual(self.call("post", "/api/auth/login",
                                           json={"email": "u@t.com", "password": "bad"}).status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
