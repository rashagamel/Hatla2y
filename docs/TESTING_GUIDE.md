# Testing Guide — Hatla2y

A complete walkthrough for running this project locally, testing every
part of it, and measuring its actual performance. Follow in order the
first time — later sections assume earlier ones already work.

---

## 0. Prerequisites

```bash
python3 --version   # need 3.9+
pip --version
```

Install dependencies:

```bash
pip install flask numpy scikit-learn --break-system-packages
```

(Drop `--break-system-packages` if you're in a virtualenv — recommended:)

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install flask numpy scikit-learn
```

Unzip the project and `cd` into it:

```bash
unzip realestate-copilot.zip
cd realestate-copilot
```

---

## 1. Confirm the data is already there

This zip ships with real data already loaded — you shouldn't need to
re-seed anything to do a first test run.

```bash
cd app
python3 -c "
from db import get_connection
conn = get_connection()
print('properties:', conn.execute('SELECT COUNT(*) c FROM properties').fetchone()['c'])
print('leads:', conn.execute('SELECT COUNT(*) c FROM leads').fetchone()['c'])
print('by source:', dict((r['data_source'], r['c']) for r in conn.execute('SELECT data_source, COUNT(*) c FROM properties GROUP BY data_source')))
"
```

**Expected output:**
```
properties: 18010
leads: 5
by source: {'real_kaggle': 18010}
```

If you see `properties: 40` and `by source: {'synthetic': 40}` instead,
the real data wasn't included or got reset — see Section 7 to re-ingest it,
or just continue testing on the synthetic 40 (everything below still works,
scores/matches will just look different).

---

## 2. Start the server and open the dashboard

```bash
python3 api.py
```

You should see:
```
* Running on http://127.0.0.1:5050
```

Open **http://localhost:5050** in a browser. If port 5050 is already in
use, edit the last line of `app/api.py` (`app.run(host="0.0.0.0",
port=5050, ...)`) to a free port.

Leave this running in its own terminal for everything below — open a
**second terminal** for the CLI testing steps.

---

## 3. Test the dashboard, tab by tab

### Data mode badge (top of sidebar)
- Confirm you see three numbers: **Demo Leads** (5), **Real Leads** (0
  initially), **Real Properties** (18,010). This is the honest indicator of
  what's actually real vs. demo data at any moment — check it after any
  test below that adds/deletes leads.

### Conversation Intelligence (Phase 1)
- Pick a lead with an incomplete profile (or create a new one via "+ Add
  New Lead" leaving most fields blank) — confirm you see an intelligence
  banner listing what's missing and a suggested next question
- Switch to Chat sim, send: `I need a 3 bedroom in New Cairo, max 25k
  monthly, I can pay 500k down and want something ready` — switch back to
  Matches, confirm: bedrooms/location/payment/monthly/down-payment/delivery
  preference all populated, missing-fields banner shrinks accordingly
- Send a follow-up: `the monthly payment is a bit too high, need it ASAP`
  — confirm an objection chip appears (monthly payment) and urgency
  updates to high, **without** erasing the values from the first message
- Any value shown in italic with "(inferred)" next to it means the number
  was derived/estimated, not directly stated — e.g. send `budget around 5
  million` and check the Budget cell

### Add / edit / delete a lead
- Click **"+ Add New Lead"** in the sidebar. Fill in a name (required) and
  a few other fields, click "Create Lead".
- Confirm the new lead appears in the sidebar list and the **Real Leads**
  count in the data mode badge goes from 0 to 1 — leads created through
  this form are real leads (an agent's actual customer), not demo data.
- Click the new lead, click **"Edit"** in the top-right, change a field,
  save — confirm it updates.
- Click **"Delete"** — confirm it's removed from the sidebar and the Real
  Leads count drops back down.

### Pipeline stage stepper
- On any lead's detail view, click through the stage buttons (new →
  contacted → viewing booked → negotiating → won/lost). Confirm the active
  stage highlights and persists after switching to another lead and back.
- Note this is separate from the outcome-logging buttons further down the
  page (see below) — the stepper is a direct manual override, the outcome
  buttons also log an event that feeds the scoring model's feedback loop.

### Matches tab (default view)
- The sidebar lists 5 demo leads (Ahmed Hassan, Sara Adly, Mohamed Farouk,
  Nourhan Adel, Karim Reda). Click each one.
- For each lead, confirm you see: a lead score (0-100), a conversion
  probability, a colored Next Best Action card (call/WhatsApp/wait) with a
  reason and often a suggested message, and up to 5 matched properties with
  green checkmarks (reasons) and amber warnings.
- **Try the outcome buttons** (Mark contacted, Viewing booked, Won, Lost,
  etc.) at the bottom of the action card — click one, confirm the pipeline
  stepper above updates to match.

### Chat sim tab
- Click a lead, switch to "Chat sim".
- Click one of the gray suggestion chips at the top — it should send the
  message and show `✓ extracted: field=value` under it within a second or
  two.
- Type your own message, e.g. `Looking for a 3 bedroom in New Cairo, cash,
  budget 6 million` — press Enter — confirm it extracts `budget_max`,
  `min_bedrooms`, `payment_preference`, `preferred_locations`.
- Switch back to the Matches tab for the same lead — confirm the matches
  changed based on what you just typed.

### Pipeline tab
- Confirm you see 3 summary numbers (total leads, pipeline value,
  probability-weighted forecast) and a status breakdown bar chart.
- Click "↻ Retrain scoring model from logged outcomes" — wait for the
  confirmation message. (This does something meaningful once you've logged
  outcomes in step 3's Matches tab — see Section 6.)

If anything in this section doesn't work, check the terminal running
`api.py` for a Python traceback and share it — don't just retry blindly.

---

## 3b. Test performance — don't just assume it's fast

The dashboard used to hang indefinitely on a real 18,010-property inventory
due to a real N+1 query bug (fixed — see README's Phase 6 section for what
it was). Use this section to verify performance yourself rather than take
that fix on faith.

### Quick check: the benchmark script

```bash
cd app
python3 ../scripts/benchmark.py
```

This starts its own server, hits every endpoint 3 times, and reports
avg/min/max latency against pass/fail thresholds — including a **concurrent
load test** that fires the same 3 requests the dashboard fires when you
click a lead (matches + score + next-action at once), because that
combined wall time is what you actually feel as a user, not any single
endpoint in isolation.

**Expected output** (numbers will vary by machine, but should be in this
ballpark):
```
=== Individual endpoint latency (avg of 3 calls) ===
  [PASS] GET /api/leads                                          avg=0.02s
  [PASS] GET /api/leads/1/matches                                avg=0.5s
  [PASS] POST /api/leads/1/score                                 avg=0.04s
  [PASS] POST /api/leads/1/next-action                           avg=0.4s
  [PASS] GET /api/stats                                          avg=0.01s
  [PASS] GET /api/pipeline                                       avg=0.002s

=== Realistic dashboard load (concurrent, like a real lead click) ===
  [PASS] dashboard concurrent load (matches+score+next-action)   total=1.0s

7/7 checks within threshold.
```

If you see `SLOW` on the concurrent load test specifically, check two
things before assuming something's broken:
1. `app/api.py`'s last line has `threaded=True` — without it, Flask's dev
   server processes the 3 concurrent requests one at a time instead of in
   parallel, which is exactly the bug that got fixed
2. `app/matching.py` has a `get_all_property_plans()` function that bulk-
   fetches installment plans in one query — if you're on an older copy of
   the code with a per-property query loop instead, that's the N+1 bug

### Testing against an already-running server

If you already have `python3 api.py` running in another terminal:

```bash
python3 ../scripts/benchmark.py --no-server
```

### Testing "does it actually feel fast" in the browser

Numbers from a script are one thing — also just click through leads in the
dashboard itself and pay attention:
- Clicking between leads in the Matches tab should feel close to instant
  (under ~1.5s), not have a visible stuck/loading period
- Typing a chat message in the Chat sim tab and pressing Enter should show
  the extraction result within 1-2 seconds
- Switching to the Pipeline tab should load immediately (it's a fast query,
  no per-property computation)

If the browser experience feels slower than the benchmark numbers suggest,
that points to something client-side or network-related rather than the
backend — check the browser's dev tools Network tab (F12) for what's
actually taking time.

---

## 4. Test each phase from the command line (no browser needed)

In your second terminal, `cd realestate-copilot/app`:

```bash
# Phase 1 — matching
python3 matching.py 1
# Expect: 5 ranked properties with reasons/warnings printed

# Phase 2 — extraction
python3 extraction.py
# Expect: 3 sample messages, each printed with its extracted JSON profile

# Phase 3 — scoring (also retrains the model)
python3 scoring.py
# Expect: training accuracy printed, then a score + top factors for each of the 5 leads

# Phase 4 — next best action
python3 next_action.py
# Expect: an action (call/whatsapp/wait), urgency, and reasoning for each lead

# Phase 5 — feedback loop
python3 feedback.py
# Expect: a pipeline summary JSON, then a retrain confirmation
```

Each of these should run cleanly with no traceback. If `scoring.py` or
`next_action.py` fail with a "model not found" error, run `python3
scoring.py` first — it trains and saves the model other scripts depend on.

---

## 4b. Test the model reliability / data maturity indicator

```bash
cd tests   # or run from the project root with `python3 -m unittest tests.test_model_maturity -v`
python3 test_model_maturity.py
```

23 tests: every maturity threshold boundary (0/4/9/10/24/25/49/50/99/100),
structural checks, a purity check (confirms the function has no side
effects on the database or scoring model), and integration tests
confirming `/api/stats`'s pre-existing fields are still present alongside
the new ones, plus `/score`, `/pipeline`, the dashboard HTML, and Phase 1
extraction all still work. Expect `Ran 23 tests ... OK`.

In the dashboard: the Pipeline tab shows a "Model Status" card, and every
lead's score in the Matches tab shows a small reliability note underneath
it. Both should read "VERY LOW DATA" / "Very Low reliability (0 real
outcomes)" on a freshly-seeded database — log a few outcomes (Section 6
below) and refresh to watch the numbers change.

## 5. Test the real-data evaluation (Phase 2 QA)

```bash
python3 eval_extraction.py
```

**Expected output:** a precision/recall/F1 table across 8 fields, computed
against the 100 labeled conversations in `data/synthetic/conversations_labeled.json`.
You should see numbers roughly matching what's in the main `README.md`
(`payment_preference` F1 ~0.91, `budget_max` F1 ~0.39, etc.) — if you're
getting very different numbers, something changed in `extraction.py` or
the dataset; that's worth investigating, not ignoring.

To test the (much stronger) LLM extraction path instead of the regex
fallback:

```bash
ANTHROPIC_API_KEY=sk-your-key python3 eval_extraction.py
```

---

## 6. Test the feedback loop end-to-end

This confirms outcomes actually influence the model, not just get logged.

```bash
cd ../app
python3 -c "
from feedback import log_outcome, pipeline_summary
log_outcome(1, 'won', 'test outcome')
log_outcome(2, 'lost', 'test outcome')
print(pipeline_summary())
"
```

Confirm the printed `status_breakdown` includes `won: 1` and reflects your
test data. Then:

```bash
python3 -c "from feedback import retrain_from_outcomes; retrain_from_outcomes()"
```

Confirm it prints something like `Retrained with 2 real outcome(s)...` —
if it says "No real outcomes logged yet," the `log_outcome` calls above
didn't take, check for a traceback.

**Reset afterward** if you don't want this test data lingering:
```bash
rm -f ../data/processed/copilot.db ../data/processed/lead_scoring_model.pkl
python3 seed.py
cd ../scripts && python3 ingest_kaggle.py --csv /path/to/your/kaggle.csv --reset-db
cd ../app && python3 -c "from db import get_connection; from seed import seed_leads; c=get_connection(); seed_leads(c)"
for i in 1 2 3 4 5; do python3 matching.py $i > /dev/null; done
python3 scoring.py > /dev/null
```

---

## 7. Test the real-data ingestion scripts

Skip this section if Section 1 already confirmed 18,010 real properties —
these are for re-running or re-verifying ingestion from scratch.

```bash
cd ../scripts
```

**Kaggle property data** (needs the CSV downloaded from Kaggle first):
```bash
python3 ingest_kaggle.py --csv /path/to/egypt_real_estate_listings.csv --limit 500
```
Test with `--limit 500` first (fast) before running the full file. Confirm
it prints `Inserted ~480 real properties...` with only small skip counts.
Drop `--limit` and add `--reset-db` for the full real run.

**PII validation gate** — test that it correctly passes clean data and
blocks bad data:
```bash
python3 validate_data.py ../docs/data_request_template.csv
# Expect: "PASS — safe to ingest"

echo 'lead_id,customer_name,notes
L1,Ahmed Test,"call 01012345678"' > /tmp/bad_test.csv
python3 validate_data.py /tmp/bad_test.csv
# Expect: "FAIL — do not ingest yet", with errors naming the phone number and 'customer_name' column
```

If the second command doesn't fail, something's wrong with the validator —
don't proceed to real brokerage data until this test passes correctly.

**Brokerage lead ingestion** (only after you have a real, validated CSV):
```bash
python3 ingest_brokerage_data.py /path/to/real_export.csv
```
This runs the validator internally first and refuses to proceed if it
fails — you should never see leads land in the database from a file that
didn't pass validation.

---

## 8. Full reset (start completely clean)

If you want to wipe everything and rebuild from scratch:

```bash
cd realestate-copilot
rm -f data/processed/copilot.db data/processed/lead_scoring_model.pkl
rm -f data/synthetic/conversations_labeled.json

cd app
python3 seed.py                              # 40 synthetic properties + 5 leads
for i in 1 2 3 4 5; do python3 matching.py $i > /dev/null; done
python3 scoring.py > /dev/null

cd ../scripts
python3 generate_synthetic_data.py            # rebuilds the 100 labeled conversations

# optional — re-add real property data:
python3 ingest_kaggle.py --csv /path/to/csv --reset-db
cd ../app && python3 -c "from db import get_connection; from seed import seed_leads; c=get_connection(); seed_leads(c)"
for i in 1 2 3 4 5; do python3 matching.py $i > /dev/null; done
python3 scoring.py > /dev/null
```

---

## Troubleshooting checklist

| Symptom | Likely cause |
|---|---|
| `ModuleNotFoundError: No module named 'flask'` | Dependencies not installed — see Section 0 |
| `sqlite3.OperationalError: no such table` | DB was reset without re-seeding — run `python3 seed.py` |
| Server starts but dashboard shows no leads | Check the browser console (F12) for a fetch error; confirm `api.py`'s terminal shows no traceback |
| `ingest_kaggle.py` skips almost everything | Check the CSV's actual column headers match what's expected — see the module docstring in `ingest_kaggle.py` |
| Scores look identical for every lead | The model wasn't retrained after a DB reset — run `python3 scoring.py` |
| Port 5050 already in use | Another process is using it — change the port in `api.py`'s last line, or kill the other process |
| Clicking a lead shows "running matching + scoring + next-best-action…" and never resolves | **Fixed as of this build** — was a real N+1 query bug (18,010 queries per match request against the real property dataset) plus Flask's dev server processing the 3 concurrent dashboard requests serially. If you still see this, confirm `app/api.py`'s last line has `threaded=True` and that `matching.py` has the `get_all_property_plans` bulk-fetch function — you may be on an older copy of the code |

If something fails and the error isn't in this table, copy the full
traceback (not just the last line) — that's what actually helps debug it.
