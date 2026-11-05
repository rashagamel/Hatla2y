# Hatla2y (هتلاقي)

AI sales copilot for Egyptian real-estate agents. It reads a client's WhatsApp-style message (Arabic or English), builds a structured profile, matches it against real listings including instalment limits, scores the lead with an explanation, and suggests the next step.

> Status: working prototype, ready for a pilot with 1–2 brokers. Not yet measured on real conversations.
> The project was previously named Tasahel, so environment variables keep the `TASAHEL_` prefix.

## What it does

| Feature | Summary |
|---|---|
| Message understanding | Extracts budget, bedrooms, area, payment type, monthly and down-payment limits, and objections from Arabic or English text (rule-based). |
| Matching | Searches ~16,865 deduplicated listings. Funnel: location → bedrooms → budget → instalment plan. Says plainly when nothing fits. |
| Instalments | Plans are estimated as (price − down payment) / months, with no interest. |
| Lead scoring | A score with human-readable reasons. Lead age is a model feature, so scores drift down over time. Model reliability is shown as "Very Low" until enough real outcomes exist. |
| Next action | Suggested next step and a ready-to-send message for each lead. |
| Pipeline | Stages from new lead to closed or lost, with lost reasons and history. |
| Analytics | Manager dashboard, conversion funnel, agent leaderboard, data quality. |
| Valuation | Indicative property value estimate (R² 0.61, MAPE ~43%), so treat it as a rough guide only. |
| Access control | Roles `admin`, `manager`, `agent`. Agents see only their own and unassigned leads. |
| WhatsApp webhook | Built (HMAC signature check) but not connected to a live number. |

## Project layout

```
app/        Flask API and logic (api.py, matching, scoring, extraction, pipeline, auth, ...)
static/     Dashboard (index.html), login page, icons
scripts/    Data loading and utilities (prepare_db.py, dedupe_properties.py, ...)
data/       Listings CSV (data/real/egypt_real_estate_listings.csv)
tests/      pytest suite (306 tests)
docs/       Pilot pitch, data policy, data dictionary, testing guide
```

## Quick start (local demo)

Requires Python 3.10+.

```powershell
# 1. Install
pip install -r requirements.txt

# 2. Build the database and load the listings
py scripts\prepare_db.py

# 3. Run
py app\api.py
```

Open http://localhost:5050. Without `TASAHEL_REQUIRE_AUTH` the dashboard runs in demo mode as "Guest" and seeds sample agents.

## Run with login (production-like)

Set these first (PowerShell; repeat in every new window):

```powershell
$env:COPILOT_DB_PATH = "C:\path\to\copilot.db"
$env:TASAHEL_REQUIRE_AUTH = "1"
$env:SECRET_KEY = "a-random-string-of-at-least-32-characters"
$env:TASAHEL_BOOTSTRAP = "1"
$env:TASAHEL_ADMIN_EMAIL = "you@yourcompany.com"
$env:TASAHEL_ADMIN_PASSWORD = "a-long-private-password"
py app\api.py
```

Log in with that admin account. After the first login, remove `TASAHEL_ADMIN_PASSWORD` and `TASAHEL_BOOTSTRAP`. In this mode no demo agents or default passwords are created.

## Configuration

Copy `.env.example` to `.env` (never commit it) or set these in your host's settings.

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Signs login tokens. Required when auth is on. Use 32+ random characters. |
| `TASAHEL_REQUIRE_AUTH` | `1` = every API call needs login. Use in production. |
| `TASAHEL_BOOTSTRAP` | `1` = create tables and the first admin on start (first deploy only). |
| `TASAHEL_ADMIN_EMAIL` / `TASAHEL_ADMIN_PASSWORD` | The one admin created on first start. |
| `COPILOT_DB_PATH` | SQLite file location. Use a persistent disk in production. |
| `TASAHEL_SEED_CSV` | Loads listings automatically when the properties table is empty. |
| `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET` | WhatsApp webhook (not connected yet). |
| `ANTHROPIC_API_KEY` | Reserved for LLM extraction (not used yet). |
| `PORT` | Server port (default 5050). |

## Deploy

Build command: `pip install -r requirements.txt && python scripts/prepare_db.py`
Start command: see `Procfile` (`gunicorn --chdir app --preload -w 2 -b 0.0.0.0:$PORT api:app`; use `-w 1` on small free tiers).

Free hosting tiers usually have an ephemeral disk, so the database resets on restart. For a real pilot, attach a persistent disk and point `COPILOT_DB_PATH` at it.

## Tests

```powershell
pip install pytest
py -m pytest tests -q
```

306 tests. `tests/conftest.py` clears server env vars so results do not depend on your shell.

## Known limitations

- Extraction is rule-based, and objection replies are English only.
- Instalment plans are estimates, not the developer's exact terms.
- Valuation is indicative only (high error).
- Lead-score accuracy and forecasting need about 30 real outcomes before they mean anything.
- WhatsApp is not connected. Phone numbers are not normalised.
- Login rate limiting is per process. Tokens cannot be revoked before expiry.

## Roadmap

1. Pilot with 1–2 brokers and collect real conversations and outcomes.
2. Measure extraction and matching accuracy on that data.
3. LLM-based extraction and Arabic replies.
4. Connect WhatsApp.
5. Persistent hosting and a proper database.

## Data and privacy

Never commit `.env`, `*.db`, or any real customer data. See `docs/DATA_PROCESSING_POLICY.md` and `docs/DATA_PROCESSING_AGREEMENT.md`. Demo data in this repo is sample data only.
