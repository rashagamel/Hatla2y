# Data Collection Process

How data gets from a source into `data/processed/copilot.db`, for each of
the three tracks. Read `DATA_DICTIONARY.md` alongside this for what actually
lands in each table.

```text
Track A: Real property data
  Kaggle CSV / brokerage inventory export
        ↓
  scripts/ingest_kaggle.py  (or a brokerage-specific variant)
        ↓
  properties + installment_plans tables (data_source = real_kaggle / real_brokerage)

Track B: Conversations
  scripts/generate_synthetic_data.py  (synthetic, safe to publish)
        ↓
  data/synthetic/conversations_labeled.json
        ↓
  app/eval_extraction.py  →  real precision/recall/F1 numbers

Track C: Real brokerage leads + outcomes
  Brokerage CSV export (docs/data_request_template.csv schema)
        ↓
  scripts/validate_data.py   ← MANDATORY gate, checks schema + scans for PII
        ↓ (only if it passes)
  scripts/ingest_brokerage_data.py
        ↓
  leads + lead_events + outcomes tables (data_source = real)
        ↓
  app/feedback.py: retrain_from_outcomes()  →  model_version updates,
  real outcomes weighted 8x vs. synthetic rows (see feedback.py docstring)
```

## Track A — Real property data ✅ DONE

1. **Kaggle dataset** ("Egyptian Real Estate Listings" by hassankhaled21,
   ~19,924 rows): downloaded by the user, ingested via
   `scripts/ingest_kaggle.py`, which was rewritten against the dataset's
   real columns once they were known (`url, price, description, location,
   type, size, bedrooms, bathrooms, available_from, payment_method,
   down_payment`) — the earlier generic column-guessing version was
   replaced, not extended, since guessing was no longer necessary.

   **Result: 18,010 real properties ingested** (`data_source='real_kaggle'`),
   covering 14 real submarkets (not just the original 4). ~1,914 rows were
   dropped: unrecognized locations (1,095), missing prices (429), a
   price-sanity filter (324 — see below), and unparsable bedroom counts (66).

   **Real finding, not hypothetical:** the ingestion surfaced actual
   data-quality problems in the source data — a small number of listings
   had implausible prices (e.g. a 130 sqm New Cairo apartment at 600,000
   EGP, ~13x below market). `MIN_PLAUSIBLE_PSQM`/`MAX_PLAUSIBLE_PSQM` in
   `ingest_kaggle.py` filter these out, informed by the dataset's own price
   distribution (1st/99th percentiles) plus independently-researched market
   ranges.

   The matching engine (`matching.py`) required **zero changes** to work on
   this real data — confirmed by running it against all 5 synthetic demo
   leads after ingestion.

2. **Brokerage inventory** (if a brokerage shares live listings, not just
   lead outcomes): write a brokerage-specific ingestion script. The pattern
   in `ingest_kaggle.py` (parse real columns directly once known, bucket
   locations by keyword match, skip rather than guess on ambiguous rows,
   document every judgment call in the docstring) is the template to copy,
   not the specific column logic — every source's schema will differ.
3. Tag `properties.data_source` correctly (`real_kaggle` / `real_brokerage`)
   so later analysis can separate real listings from any remaining
   synthetic ones without guessing.

`app/seed.py`'s synthetic property prices (used for the 40 fallback
properties, e.g. via `python3 seed.py` on a fresh DB) were independently
calibrated to real market data before any real listing data was available
— see `price_for()`'s docstring. The real Kaggle data landed close to those
estimates (New Cairo: 59,536 EGP/sqm real vs. 61,550 estimated), which is a
useful cross-check on both.

## Track B — Conversations

`scripts/generate_synthetic_data.py` produces 100 labeled conversations at
a 40/20/20/20 Arabic/English/Arabizi/Mixed split. This is deliberately
**templated synthetic data, not real WhatsApp exports** — real
conversations should never be published, even anonymized, without explicit
consent from the people in them. If you get real conversation summaries
from a brokerage (Track C), evaluate the extractor against those directly
instead of publishing them alongside this repo.

`app/eval_extraction.py` computes real precision/recall/F1 per field. Rerun
it any time you change `app/extraction.py`, and rerun it again once you set
`ANTHROPIC_API_KEY` — the numbers in `README.md` right now are for the
heuristic fallback only.

## Track C — Real brokerage leads + outcomes

1. Use `docs/BROKERAGE_PITCH.md` to reach out.
2. When you get a CSV back, run it through `scripts/validate_data.py`
   first — always, even if they say it's already anonymized. It checks
   column names and cell contents for phone/email/national-ID patterns and
   refuses to pass anything suspicious.
3. Only after validation passes: `scripts/ingest_brokerage_data.py`. It
   re-runs the validator internally as a hard gate — there's no flag to
   skip it.
4. Real leads land with `data_source='real'`. Run
   `python3 feedback.py` (or call `retrain_from_outcomes()` directly) to
   blend the real outcomes into the scoring model. Check
   `lead_scores.model_version` afterward — it should still say something
   synthetic-based until you've actually retrained.

## What "processed" means

`data/processed/` holds the live SQLite DB and trained model — the actual
working state of the system, built from `data/synthetic/` and whatever
lands in `data/real/` (raw exports, kept there before ingestion, not
committed to version control). Treat `data/processed/*.db` as regenerable
from scratch by rerunning the seed/ingest scripts, not as a hand-edited
source of truth.
