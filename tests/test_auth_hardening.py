"""
Tests for the hardened app/auth.py: no well-known passwords in production, mandatory SECRET_KEY,
password/role validation, and only admins may create admins.

Run: py -m pytest tests/test_auth_hardening.py -q
"""
import importlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from db import get_connection  # noqa: E402
from test_base import IsolatedDatabaseTestCase  # noqa: E402

GOOD_KEY = "k" * 40


class AuthTestCase(IsolatedDatabaseTestCase):

    def setUp(self):
        conn = get_connection()
        conn.execute("DELETE FROM users")
        conn.commit()
        conn.close()
        patcher = mock.patch.dict(os.environ, {"SECRET_KEY": GOOD_KEY, "TASAHEL_REQUIRE_AUTH": "0"})
        patcher.start()
        self.addCleanup(patcher.stop)
        import auth
        self.auth = importlib.reload(auth)
        self.addCleanup(lambda: importlib.reload(auth))

    def user_emails(self):
        return {u["email"] for u in self.auth.list_users()}


class TestSeeding(AuthTestCase):

    def test_production_never_creates_well_known_accounts(self):
        with mock.patch.dict(os.environ, {"TASAHEL_REQUIRE_AUTH": "1"}):
            os.environ.pop("TASAHEL_ADMIN_EMAIL", None)
            os.environ.pop("TASAHEL_ADMIN_PASSWORD", None)
            self.auth.seed_default_users()
        self.assertEqual(self.user_emails(), set())

    def test_production_creates_only_the_admin_from_the_environment(self):
        env = {"TASAHEL_REQUIRE_AUTH": "1", "TASAHEL_ADMIN_EMAIL": "Owner@Firm.com",
               "TASAHEL_ADMIN_PASSWORD": "a-long-private-pass"}
        with mock.patch.dict(os.environ, env):
            self.auth.seed_default_users()
        self.assertEqual(self.user_emails(), {"owner@firm.com"})
        result = self.auth.authenticate_user("OWNER@firm.com", "a-long-private-pass")
        self.assertEqual(result["user"]["role"], "admin")
        with self.assertRaises(ValueError):
            self.auth.authenticate_user("manager@tasahel.com", "manager123")

    def test_seeding_is_skipped_once_users_exist(self):
        self.auth.create_user("x@y.com", "longenough1", "X", role="manager")
        self.auth.seed_default_users()
        self.assertEqual(self.user_emails(), {"x@y.com"})

    def test_local_demo_mode_keeps_the_demo_accounts(self):
        self.auth.seed_default_users()
        self.assertEqual(self.user_emails(), {"manager@tasahel.com", "agent@tasahel.com"})


class TestSecretKey(AuthTestCase):

    def test_production_refuses_to_start_without_a_strong_key(self):
        import auth
        for bad in ("", "short"):
            with mock.patch.dict(os.environ, {"TASAHEL_REQUIRE_AUTH": "1", "SECRET_KEY": bad}):
                with self.assertRaises(RuntimeError):
                    importlib.reload(auth)

    def test_production_starts_with_a_strong_key(self):
        import auth
        with mock.patch.dict(os.environ, {"TASAHEL_REQUIRE_AUTH": "1", "SECRET_KEY": GOOD_KEY}):
            importlib.reload(auth)
            self.assertEqual(auth.SECRET_KEY, GOOD_KEY)

    def test_local_mode_still_starts_without_a_key(self):
        import auth
        with mock.patch.dict(os.environ, {"TASAHEL_REQUIRE_AUTH": "0", "SECRET_KEY": ""}):
            importlib.reload(auth)
            self.assertTrue(len(auth.SECRET_KEY) >= 32)


class TestValidation(AuthTestCase):

    def test_short_password_rejected(self):
        with self.assertRaises(ValueError):
            self.auth.create_user("a@b.com", "short", "A")

    def test_overlong_password_rejected_instead_of_crashing(self):
        with self.assertRaises(ValueError):
            self.auth.create_user("a@b.com", "p" * 100, "A")

    def test_unknown_role_rejected_on_create_and_update(self):
        with self.assertRaises(ValueError):
            self.auth.create_user("a@b.com", "longenough1", "A", role="superuser")
        user = self.auth.create_user("a@b.com", "longenough1", "A")
        with self.assertRaises(ValueError):
            self.auth.update_user(user["id"], {"role": "superuser"})

    def test_bad_email_and_blank_name_rejected(self):
        with self.assertRaises(ValueError):
            self.auth.create_user("not-an-email", "longenough1", "A")
        with self.assertRaises(ValueError):
            self.auth.create_user("a@b.com", "longenough1", "  ")

    def test_change_password_enforces_the_same_rule(self):
        user = self.auth.create_user("a@b.com", "longenough1", "A")
        with self.assertRaises(ValueError):
            self.auth.change_password(user["id"], "longenough1", "x")
        self.auth.change_password(user["id"], "longenough1", "anotherlong2")
        self.auth.authenticate_user("a@b.com", "anotherlong2")

    def test_wrong_email_and_wrong_password_give_the_same_message(self):
        self.auth.create_user("a@b.com", "longenough1", "A")
        messages = set()
        for email, pw in (("a@b.com", "wrongwrong"), ("nobody@b.com", "longenough1")):
            with self.assertRaises(ValueError) as ctx:
                self.auth.authenticate_user(email, pw)
            messages.add(str(ctx.exception))
        self.assertEqual(len(messages), 1)

    def test_deactivated_user_cannot_log_in(self):
        user = self.auth.create_user("a@b.com", "longenough1", "A")
        self.auth.update_user(user["id"], {"is_active": False})
        with self.assertRaises(ValueError):
            self.auth.authenticate_user("a@b.com", "longenough1")


class TestOnlyAdminsMakeAdmins(AuthTestCase):

    def setUp(self):
        super().setUp()
        import api
        self.api = api
        self.client = api.app.test_client()
        self.actor = {"id": 1, "email": "m@t.com", "name": "M", "role": "manager", "agent_id": None}

        def fake_require_manager(req):
            return dict(self.actor), None

        for p in (mock.patch.object(api, "require_manager", fake_require_manager),
                  mock.patch.object(api, "require_auth", lambda r: (dict(self.actor), None))):
            p.start()
            self.addCleanup(p.stop)

    def body(self, role):
        return {"email": f"{role}@t.com", "password": "longenough1", "name": "N", "role": role}

    def test_manager_cannot_create_an_admin(self):
        resp = self.client.post("/api/users", json=self.body("admin"))
        self.assertEqual(resp.status_code, 403)

    def test_manager_can_create_an_agent(self):
        resp = self.client.post("/api/users", json=self.body("agent"))
        self.assertEqual(resp.status_code, 201)

    def test_manager_cannot_promote_to_admin(self):
        user = self.auth.create_user("p@t.com", "longenough1", "P")
        resp = self.client.patch(f"/api/users/{user['id']}", json={"role": "admin"})
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_create_an_admin(self):
        self.actor["role"] = "admin"
        resp = self.client.post("/api/users", json=self.body("admin"))
        self.assertEqual(resp.status_code, 201)


class TestRealTokenRoundTrip(AuthTestCase):
    """No fakes: log in, then call the API with the token the server just issued.
    (Caught a real bug: PyJWT >= 2.10 rejects an integer 'sub', so every login was followed by a 401.)"""

    def setUp(self):
        super().setUp()
        import api
        self.client = api.app.test_client()
        env = mock.patch.dict(os.environ, {"TASAHEL_REQUIRE_AUTH": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.user = self.auth.create_user("rt@t.com", "longenough1", "RT", role="manager")
        self.token = self.auth.authenticate_user("rt@t.com", "longenough1")["token"]

    def get(self, token):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.get("/api/leads", headers=headers)

    def test_fresh_login_token_is_accepted(self):
        self.assertEqual(self.get(self.token).status_code, 200)

    def test_login_endpoint_then_api_call(self):
        resp = self.client.post("/api/auth/login", json={"email": "rt@t.com", "password": "longenough1"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.get(resp.get_json()["token"]).status_code, 200)

    def test_tampered_and_missing_tokens_are_rejected(self):
        self.assertEqual(self.get(self.token + "x").status_code, 401)
        self.assertEqual(self.get(None).status_code, 401)

    def test_token_stops_working_when_the_user_is_deactivated(self):
        self.auth.update_user(self.user["id"], {"is_active": False})
        self.assertEqual(self.get(self.token).status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)