# Data Dictionary

Every table and field in `data/processed/copilot.db`. Generated from
`app/db.py`'s schema — if the two ever disagree, `db.py` is the source of
truth; update this file to match it, not the other way around.

## `properties`

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| title | TEXT | Display name, e.g. "Apartment in Mivida" |
| compound | TEXT | e.g. "Mivida", "Marassi" |
| location | TEXT | Real data (18,010 rows) spans 14 submarkets: New Cairo, 6th of October, Sheikh Zayed, North Coast, Red Sea, Mostakbal City, Ain Sokhna, Madinaty, New Capital, Heliopolis, El Shorouk, Maadi, Nasr City, Zamalek. Synthetic seed data (`seed.py`) only uses the original 4. |
| property_type | TEXT | Synthetic: apartment \| villa \| duplex \| townhouse. Real data adds: chalet, twin_house, penthouse, hotel_apartment, cabin, palace, whole_building, roof, full_floor (see `ingest_kaggle.py`'s TYPE_MAP) |
| area_sqm | REAL | |
| bedrooms | INTEGER | |
| bathrooms | INTEGER | |
| floor | INTEGER | nullable — apartments only |
| finishing | TEXT | core \| semi-finished \| fully-finished \| luxury |
| has_pool / has_garden / has_parking / has_security | INTEGER (0/1) | |
| developer | TEXT | |
| cash_price | REAL | EGP, full cash price |
| delivery_year | INTEGER | |
| status | TEXT | available \| reserved \| sold |
| description | TEXT | |
| **data_source** | TEXT | **synthetic \| real_kaggle \| real_brokerage** — always check this before trusting a price as real-market data |
| created_at | TEXT | ISO timestamp |

## `installment_plans`

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| property_id | INTEGER FK | -> properties.id |
| years | INTEGER | plan duration |
| down_payment_pct | REAL | e.g. 10 = 10% |
| monthly_payment | REAL | EGP/month |
| total_price | REAL | installment total (usually > cash_price) |

**Note:** installment plan provenance depends on how the property was
ingested. For `real_kaggle` properties, a plan only exists if the source
listing had `payment_method == "Installments"` with a real down payment —
in that case the plan uses the REAL down payment and REAL total price, but
an ASSUMED 7-year term (the dataset doesn't include plan duration). Cash-
listed real properties have no installment_plans row at all. Synthetic
properties (from `seed.py`) always have 1-3 fully synthetic plans. There is
no `data_source` column on `installment_plans` itself — check the parent
property's `data_source` and, for real ones, `ingest_kaggle.py`'s docstring
for exactly what's real vs. assumed in each plan.

## `leads`

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT | For real imports, this is a placeholder like "Lead EXAMPLE-001" — never a real name |
| phone | TEXT | Should be NULL for all real/imported leads — see PII policy below |
| channel | TEXT | whatsapp \| facebook_ad \| call \| walk_in |
| **data_source** | TEXT | **synthetic \| real** |
| budget_min / budget_max | REAL | EGP |
| payment_preference | TEXT | cash \| installment \| unknown |
| max_monthly_payment / max_down_payment | REAL | EGP, installment buyers only |
| preferred_locations | TEXT | comma-separated |
| min_bedrooms | INTEGER | |
| purpose | TEXT | family \| investment \| rental \| unknown |
| purchase_timeline | TEXT | immediate \| 1-3m \| 3-6m \| 6m+ \| unknown |
| intent | TEXT | low \| medium \| high — manually set on synthetic seed leads only; real leads should rely on `lead_scores` instead |
| status | TEXT | new \| contacted \| viewing_booked \| negotiating \| won \| lost |
| **urgency** | TEXT | **low \| medium \| high \| unknown** — Phase 1 (Conversation Intelligence). Distinct from `purchase_timeline`: timeline is a categorical bucket of *when*, urgency also captures direct emotional/language cues ("ASAP", "no rush") — see `extraction.py`'s `_detect_urgency()` |
| **delivery_preference** | TEXT | **ready \| under_construction \| null** — Phase 1 |
| **objections** | TEXT | **JSON list**, e.g. `["monthly_payment","down_payment"]` — Phase 1. Accumulates across messages (a concern raised once isn't un-raised by a later message not mentioning it) — see `merge_profile_into_lead()` |
| **extraction_confidence** | TEXT | **JSON dict**, e.g. `{"budget_max":"explicit","urgency":"inferred"}` — Phase 1. Per-field confidence: "explicit" = directly stated, "inferred" = derived/estimated. Only covers fields present in the dict; absence means the field hasn't been extracted from any message yet |
| created_at / updated_at | TEXT | ISO timestamps |

**Note on Phase 1 fields:** `missing_fields` and `suggested_next_question` are NOT stored
columns — they're computed fresh on every `GET /api/leads/<id>` call from
`extraction.compute_missing_fields()` / `suggest_next_question()`, specifically so they
can never go stale relative to the lead's current profile state.

## `lead_events`

Raw activity log — every message, view, and viewing feeds scoring.

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| lead_id | INTEGER FK | |
| event_type | TEXT | message_in \| message_out \| property_viewed \| call \| viewing_booked \| outcome |
| payload | TEXT | free text — for real imports, this is `conversation_summary` from the brokerage export, already stripped of PII by `validate_data.py` before ingestion |
| created_at | TEXT | ISO timestamp |

## `matches`

Cached matching-engine output (Phase 1). Regenerated every time
`match_lead_to_properties()` runs — treat as a log, not a source of truth.

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| lead_id / property_id | INTEGER FK | |
| score | REAL | 0.0-1.0 |
| reasons | TEXT | JSON list of strings |
| created_at | TEXT | ISO timestamp |

## `lead_scores`

Phase 3 model output history.

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| lead_id | INTEGER FK | |
| score | REAL | 0-100 |
| conversion_probability | REAL | 0-1 |
| top_factors | TEXT | JSON list of `{factor, value, direction}` |
| model_version | TEXT | e.g. "v1-synthetic-gbc" — always check this to know whether real outcomes have been blended in yet (see `feedback.retrain_from_outcomes()`) |
| created_at | TEXT | ISO timestamp |

## `next_actions`

Phase 4 recommendation history.

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| lead_id | INTEGER FK | |
| action_type | TEXT | call \| whatsapp \| wait \| send_properties |
| channel | TEXT | whatsapp \| call \| none |
| message_suggestion | TEXT | |
| reasoning | TEXT | |
| urgency | TEXT | now \| today \| this_week \| low_priority |
| generated_by | TEXT | rule_engine \| llm |
| created_at | TEXT | ISO timestamp |

## `outcomes`

Phase 5 feedback loop input.

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| lead_id | INTEGER FK | |
| outcome | TEXT | contacted \| viewing_booked \| negotiating \| won \| lost \| no_response |
| note | TEXT | |
| property_id | INTEGER FK | nullable |
| created_at | TEXT | ISO timestamp |

---

## PII policy (applies to every table above)

- `leads.phone` should be NULL for anything not entered directly through the
  live dashboard/chat-sim by you for testing. Real brokerage imports never
  populate it — see `scripts/ingest_brokerage_data.py`.
- No table has a `customer_name`, `email`, or `national_id` field, on
  purpose. If you ever find yourself wanting to add one, stop and re-read
  `docs/brokerage_outreach.md`'s PII section first.
- Any free-text field (`lead_events.payload`, `properties.description`)
  can technically contain anything typed into it — `scripts/validate_data.py`
  scans incoming CSVs for PII patterns before ingestion, but it isn't a
  guarantee for hand-typed data entered through the dashboard. Don't paste
  real customer names/phone numbers into the chat simulator either.
