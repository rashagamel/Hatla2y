"""Test session setup.

Server settings left over in the shell (for example TASAHEL_REQUIRE_AUTH=1 from running the server
locally in the same PowerShell window) must never change how the tests behave: with auth switched on,
every API test would get 401. Each test run therefore starts from a clean environment; the tests that
need a particular setting set it themselves."""
import os

for _name in ("TASAHEL_REQUIRE_AUTH", "TASAHEL_BOOTSTRAP", "TASAHEL_ADMIN_EMAIL", "TASAHEL_ADMIN_PASSWORD",
              "TASAHEL_SEED_CSV", "TASAHEL_INGEST_CSV", "COPILOT_DB_PATH", "SECRET_KEY",
              "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_APP_SECRET"):
    os.environ.pop(_name, None)
