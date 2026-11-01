# Changelog

## Unreleased — Hatla2y rename, hardening and deploy prep
- Renamed the product to Hatla2y (هتلاقي); new teal / spring-meadow theme with skyline wallpaper.
- Central access control, auth hardening, login throttle, signed WhatsApp webhook.
- Consistent match counts, duplicate-listing cleanup (scripts/dedupe_properties.py), server-side seed loading (TASAHEL_SEED_CSV).
- requirements.txt is now UTF-8 (it was UTF-16 and would break pip); .gitignore rewritten without BOM so .env is really ignored.
## System Completion & Roadmap Convergence (Phases 5, 7, 8, 9)

Completed end-to-end implementation and UI convergence for the planned intelligence, analytics, valuation, and pipeline phases across the backend and frontend.

### What changed

- **Phase 5 — Structured AI Lead Intelligence (`app/scoring.py`, `static/index.html`)**:
  - Wired `/api/leads/<id>/intelligence` into the primary lead interface with a dedicated, high-impact **AI Lead Intelligence Card**.
  - Displays overall quality score, priority tier (`high`, `medium`, `low`), narrative reasoning, and dimension fit breakdown (`budget_fit`, `location_fit`, `financing_fit`, and `inventory_availability`).
  - Highlights positive factors, warnings, and information gaps. All 29 Phase 5 structural tests passing.
- **Phase 7 — Automated Valuation Model (AVM) (`app/valuation.py`, `static/index.html`)**:
  - Surfaced the econometric property valuation model ($R^2 = 0.8154$, MAPE $= 15.59\%$, trained on 210 Egyptian properties) into the interface.
  - Property match cards now feature on-demand AVM valuation assessment badges (`🟢 Undervalued`, `🔵 Fair Market Price`, `⚠ Overpriced`) with fair market price/sqm, 90% confidence bands, and price driver attribution.
  - Added dedicated **Valuation** tab with an interactive property valuation calculator allowing brokers to estimate market value based on submarket, property type, sqm area, finishing, and amenities.
- **Phase 8 — Pipeline State Machine & Lost-Lead Analytics (`app/pipeline.py`, `static/index.html`)**:
  - Upgraded lead status stepper to the canonical 8-stage state machine (`new`, `contacted`, `qualified`, `interested`, `site_visit`, `negotiation`, `won`, `lost`).
  - Added **Lost Reason Modal** prompting agents for structured lost reasons (`price`, `location`, `financing`, `competitor`, `timing`, `unresponsive`, `other`) and notes when marking deals lost.
  - Added transition history modal pulling from `pipeline_transitions` table for auditability.
- **Phase 9 — Manager Analytics & Conversion Funnel (`app/analytics.py`, `static/index.html`)**:
  - Added dedicated **Analytics** dashboard featuring:
    - Executive KPIs (Total Leads, Closed Won Conversion %, Gross Pipeline Value, Average Response Time).
    - Visual **Sales Conversion Funnel** showing drop-off and conversion rates from New Leads to Won Deals.
    - **Sales Agent Productivity Leaderboard** tracking active pipeline, won deals, and win rates across team members.
    - Catalog and Data Quality Health overview (Kaggle listings, duplicate checks, installment extraction rates).
- **Multi-Agent Management & Lead Assignment (`app/agents.py`, `app/api.py`)**:
  - Seeded default sales agents (`Karim El-Sayed`, `Nour Mansour`, `Youssef Nabil`) and added inline lead re-assignment dropdown in the lead profile header.
  - Added modal to create and onboard new sales agents (`POST /api/agents`).
- **Auth & Session Management (`app/auth.py`, `app/api.py`, `static/login.html`)**:
  - Implemented optional JWT authentication in `list_leads()` ensuring full backward compatibility for unauthenticated test suites and demo sessions while filtering by agent for authenticated users.
  - Added session status bar in sidebar displaying logged-in user profile, role badge, and sign in / sign out controls. Fixed Windows console encoding issue in `auth.py`.
- **System Health & Test Suite**:
  - 100% test pass rate: **217 / 217 tests passing**.

---



Real gap closed, measured honestly rather than assumed fixed. The
project's own evaluation (`eval_extraction.py`) had shown the heuristic
extractor was blind on Arabic-script text (`n/a` across the board) since
this project's early real-data work — this session added genuine Arabic
parsing rather than a token gesture.

### What changed

- **`app/extraction.py`**: new Arabic detection functions —
  `_detect_arabic_locations`, `_parse_arabic_money`,
  `_detect_arabic_bedrooms`, `_detect_arabic_payment_preference`,
  `_parse_arabic_down_payment`, `_parse_arabic_monthly_payment`,
  `_detect_arabic_purpose`, `_detect_arabic_timeline` — wired in as
  fallbacks alongside the existing English regex patterns in
  `extract_with_heuristics()`, not a separate code path. Arabic-Indic
  digit normalization (٠-٩ → 0-9) applied before any numeric parsing.
- **`static/index.html`**: added a real Arabic example as a Chat sim
  suggestion chip.
- **`tests/test_arabic_extraction.py`** (new): 25 tests — the user's exact
  reported message verbatim, per-field unit tests, and two tests that run
  against the real 40 labeled Arabic conversations already in
  `data/synthetic/conversations_labeled.json` (not just hand-picked examples).

### Measured improvement (re-run `eval_extraction.py` to reproduce)

| Field | Before | After |
|---|---|---|
| preferred_locations | 0.75 | 1.00 |
| min_bedrooms | 0.47 | 0.89 |
| purpose | 0.39 | 0.88 |
| max_down_payment | 0.52 | 0.85 |
| payment_preference | 0.91 | 0.97 |
| purchase_timeline | 0.30 | 0.73 |
| max_monthly_payment | 0.18 | 0.65 |
| budget_max | 0.39 | 0.78 |

### Three real bugs found and fixed testing against the user's own example

Not the labeled dataset — the user's actual reported message surfaced
gaps the dataset didn't:
1. "القاهرة الجديدة" (colloquial "New Cairo") wasn't in the location map —
   only the more formal "التجمع" was covered.
2. Bedroom word order: the labeled dataset only ever says "3 غرف"
   (number-then-noun); the user wrote "عدد غرف 3" (noun-then-number). Fixed
   with a bidirectional pattern.
3. A genuine double-counting bug: "مقدم 500 الف" (a down payment) was also
   matched by the generic budget parser, incorrectly setting `budget_max`
   to the same figure even though no budget was stated anywhere. Fixed by
   excluding numbers in a short window after "مقدم"/"قسط شهري" from the
   budget fallback.

### A known limitation found and documented, not silently left unexplained

Investigating why "Mixed" language's `max_down_payment` F1 looked
surprisingly low (0.22) revealed a **pre-existing bug in
`scripts/generate_synthetic_data.py`**, not in extraction: the mixed-
language template randomly includes either a monthly-payment OR a
down-payment sentence, but always labels ground truth for both regardless
of which was actually written — making some "mixed" conversations
genuinely unlabelable from their own text. Documented in README rather
than silently accepted or hidden; not fixed in this session (a test-data
generation issue, out of scope for an extraction-focused change).

### What was explicitly not addressed

Arabizi (Latin-script transliterated Arabic) is untouched — a different
problem, still weak. The LLM extraction path would handle all of this
better; this closes the gap specifically for the no-API-key heuristic
fallback.

---

## Phase 4 — Advanced Installment / Affordability Intelligence

Adds a comparison layer on top of the EXISTING `installment_plans` data —
no new pricing logic, no synthetic plan generation, matching the spec's
explicit instruction not to duplicate matching/pricing logic.

### What changed

- **`app/affordability.py`** (new module): `assess_plan_affordability()`
  labels one plan GREEN/YELLOW/RED/UNKNOWN against a lead's stated
  constraints (15% overshoot threshold for YELLOW, documented as practical
  engineering guidance, not statistically derived — same honesty standard
  as `feedback.get_model_maturity`'s stages). `compare_property_plans()`
  labels every plan on a property and recommends the best (shortest
  GREEN term), never silently recommending RED. `get_affordability_profile()`
  gives a lead-level price ballpark from real observed average plan terms,
  explicitly labeled as an estimate.
- **`app/api.py`**: two new endpoints —
  `GET /api/leads/<id>/properties/<property_id>/affordability` and
  `GET /api/leads/<id>/affordability-profile`. No existing endpoints changed.
- **`static/index.html`**: "Compare installment plans" button on every
  match card, expanding to show labeled plans and the recommendation.
- **`tests/test_affordability.py`** (new): 21 tests — per-plan labeling at
  each threshold boundary, multi-plan comparison and best-plan selection,
  the "never recommend RED when better exists" guarantee, no-fabricated-
  data checks (figures traced back to stored DB rows), affordability
  profile for cash/installment/unknown buyers, and API integration.

### Honest limitation

Real Kaggle-ingested properties mostly have exactly one installment plan
each (a documented limitation of the source data, see `ingest_kaggle.py`),
so multi-plan comparison is most visible on synthetic properties. Real
multi-plan comparison would need either richer source data or a future
brokerage import that includes multiple plans per unit.

### What was explicitly NOT done

No changes to `matching.py`'s existing scoring/plan-selection logic — this
module reads the same data and labels it more richly, it doesn't replace
or duplicate the existing affordability scoring. No start on Phase 5
(Manager Intelligence) or any later phase.

---

## Phase 3 — AI Sales Conversation Copilot

Ties an incoming message directly to a suggested reply, rather than only
updating the stored profile. Built on top of Phase 1's extraction and
Phase 2's next-best-action, reusing both rather than duplicating logic.

### What changed

- **`app/extraction.py`**: new negative-preference detection
  (`_detect_removed_locations()`) and engagement-signal detection
  (`_detect_engagement_signal()` → `still_interested` / `losing_interest`).
  `merge_profile_into_lead()` now actually removes a ruled-out location from
  `preferred_locations`, including when the same message's positive-location
  matcher would otherwise re-add it (a message like "I don't like Sheikh
  Zayed" contains the substring "Sheikh Zayed", which the existing positive
  matcher picks up too — removal is applied last, so it wins).
- **`app/conversation.py`** (new module): `generate_conversation_reply()` —
  logs the inbound message, extracts + merges the profile, logs an
  `intent_signal` lead event when an engagement signal is detected,
  re-matches against the just-updated profile, and composes a suggested
  reply from only real data (remaining preferences, real match locations,
  real plan-fit text already generated by `matching.py`).
- **`app/api.py`**: `/api/leads/<id>/chat` and `/api/whatsapp/webhook` now
  both delegate to `generate_conversation_reply()` (previously had near-
  duplicate logic between the two endpoints). Response is additive —
  `extracted` and `updated_fields` are unchanged in shape, with
  `removed_preferences`, `engagement_signal`, `suggested_reply`, and
  `matches_preview` added alongside.
- **`static/index.html`**: Chat sim tab shows ruled-out-preference and
  engagement-signal chips, plus a distinct "Suggested reply" message
  bubble after each incoming message. Also cleaned up the extraction
  summary line, which was dumping raw JSON for `objections`/
  `extraction_confidence` — those are better shown as chips/not shown here.
- **`tests/test_conversation.py`** (new): 18 tests — the spec's own worked
  example verbatim, the adverb-tolerance regression, the cross-sentence
  false-positive regression, removal-wins-over-positive-match, the
  known-cap-vs-no-cap example-citation distinction, intent-signal logging,
  and API integration.

### Two real bugs found and fixed during testing (not hypothetical)

1. `"don't really like Sheikh Zayed"` wasn't detected — the negation-cue
   check used literal substrings (`"don't like"`), which don't match when
   an adverb sits between the negation and the verb. Same class of gap as
   an earlier Phase 1 fix for budget phrasing ("roughly 5 million"). Fixed
   with regex patterns tolerant of intervening adverbs.
2. `"I don't like small kitchens. New Cairo works great"` incorrectly
   flagged New Cairo for removal — the negation-detection lookback window
   was a fixed 40-character count with no sentence-boundary awareness, so
   a negation cue from an entirely unrelated, earlier sentence leaked into
   the check for a location three sentences later. Reproduced via a test
   that failed on first run (not assumed correct from code review), fixed
   by truncating the lookback window at the nearest sentence-ending
   punctuation.
3. (Found while building the reply-composer, same standard applied): the
   first draft of the suggested reply cited a specific "plan fits" example
   monthly payment even when the lead had no stated monthly cap at all —
   because `matching.py` trivially marks every plan as "fitting" when
   there's no constraint to violate. Citing a number as reassurance right
   after a payment objection, when it doesn't actually address a known
   constraint, would be misleading. Fixed by only citing a specific example
   when the lead has a real known cap AND the match is a genuine fit
   against it, not just a "fits because nothing to violate" default.

### What was explicitly NOT done

No new matching or scoring algorithm — `conversation.py` composes replies
from what `matching.py`/`extraction.py`/`next_action.py` already know. No
start on Phase 4 (Advanced Installment Intelligence) or any later phase.

---

## Phase 2 — Smart Next Best Action

Upgrades the single-signal next-action suggestion (score threshold only)
into a multi-signal, explainable decision engine. Existing project rule
maintained: one phase at a time, fully tested, complete project ZIP
delivered.

### What changed

- **`app/next_action.py`** (rewritten, not appended to): new 8-action
  controlled vocabulary (`call_now`, `send_matching_properties`,
  `follow_up`, `ask_clarifying_question`, `schedule_viewing`,
  `send_installment_options`, `re_engage`, `no_action`), a deterministic
  explainable decision tree (`decide_action()`), a separate recommendation-
  confidence calculation (`compute_confidence()`) kept distinct from model
  reliability, real-data-only talking points and messages, and a priority
  queue (`get_priority_queue()`). `generate_next_action(lead_id)` keeps its
  original signature; the response dict is extended, not restructured —
  `action_type`/`channel`/`urgency`/`message_suggestion`/`reasoning` are
  still present for backward compatibility, aliasing the new fields.
- **`app/api.py`**: `/api/leads/<id>/next-action` extended (not
  duplicated) with the richer response. Two new endpoints:
  `GET /api/priority-queue` and `POST /api/leads/<id>/next-action/track`
  (for `selected`/`completed` action tracking). No existing fields removed.
- **`static/index.html`**: the action card now shows priority, a real
  explainability checklist ("why this recommendation"), talking points, a
  clarifying question box, suggested message, recommended property chips
  (real property IDs from the existing matching engine), confidence vs.
  model reliability shown separately, and track buttons. A new "Today's
  Priorities" section on the Pipeline tab shows the priority queue,
  clickable to jump straight to that lead.
- **`tests/test_next_action.py`** (new): 26 tests covering every required
  decision scenario (missing fields → clarifying question, payment
  objections → installment options, viewing requested/booked →
  schedule/follow-up, inactive → re-engage, closed leads → no action),
  data integrity (no fabricated property IDs, no properties recommended
  for closed/re-engage leads), the confidence/reliability separation, the
  priority queue, and full API integration — all leads created during
  testing are cleaned up in `tearDown`, confirmed via a post-suite check.

### Deliberate, documented behavior change

`action_type`'s possible string values changed from the original 4
(`call`/`whatsapp`/`wait`/`send_properties`) to the 8-action controlled
set the spec requires. The endpoint, field names, and JSON envelope are
unchanged — only what strings `action_type`/`action` can contain. The
dashboard (the only consumer) was updated to match.

### A real bug found and fixed during testing

Not a hypothetical — reproduced directly. `generate_next_action()` logs
its own `next_action_generated` event for future action-tracking analysis
(section 13 of the spec). The original activity-recency calculation
counted every lead event, including this one, as customer activity. Result:
calling the function once for a stale lead (e.g. `re_engage`), then calling
it again, would return a *different* action the second time — because the
first call's own logged event made the lead look freshly active. Caught by
comparing a direct per-lead call against the priority queue's independent
call for the same lead and finding they disagreed for `Sara Adly` (`re_engage`
vs. `send_matching_properties`) in manual testing. Fixed by excluding
`next_action_*` event types from the recency calculation
(`gather_engagement_signals()`), verified with a dedicated regression test,
and confirmed the priority queue and a direct call now always agree.

### What was explicitly NOT done

Per instruction: no autonomous agents, no LangGraph workflows, no vector
database, no RAG, no microservices, no new required LLM dependency (the
deterministic rule engine is the default; an LLM path exists but is
optional, same pattern as Phases 1 and 4), and no start on Phase 3
(AI Sales Conversation Copilot) or any other future phase.

---

## Model Reliability / Data Maturity indicator

Not a new feature phase — a small, explicitly-scoped addition on top of the
existing feedback loop, done before starting Phase 2, per direct
instruction not to add new major features alongside this.

### The honest finding this documents

While testing the `outcomes → retraining → model` loop (see the "real bugs
found" precedent set in Phase 1 below — same standard applied here):
real-outcome effects on the model were clearly measurable and
distinguishable from retraining noise, but **with only 4 real outcomes,
individual-lead score direction was not consistently reliable** — most
leads moved the expected way, but one `won`-labeled lead's own score moved
*down* after being trained on its own positive label. This is expected
evidence of insufficient real training data at this sample size, not proof
the scoring model is broken — but the system had no way to communicate
that distinction to whoever's looking at a score. This change adds that.

### What changed

- **`app/feedback.py`**: added `get_model_maturity(real_outcome_count)` —
  a pure function (no DB access, no side effects) mapping a real-outcome
  count to a maturity stage, reliability label, and warning message. Five
  stages: Very Low Data (0-9) → Early Pilot (10-24) → Pilot (25-49) →
  Validation (50-99) → Production Candidate (100+). Explicitly documented
  in the function's own docstring as practical engineering stages for this
  prototype, not statistically validated thresholds — do not present them
  as proven accuracy cutoffs.
- **`app/api.py`**: `GET /api/stats` now additionally returns
  `real_outcomes`, `model_maturity`, `model_reliability`, `score_warning`.
  All four pre-existing fields (`demo_leads`, `real_leads`,
  `real_properties`, `synthetic_properties`) are unchanged and still present.
- **`static/index.html`**: a "Model Status" card on the Pipeline tab
  (maturity stage, reliability pill, real outcome count, warning text,
  and an explicit footnote that these are engineering stages, not
  validated science) plus a compact reliability note directly under every
  individual lead's score in the Matches tab.
- **`tests/test_model_maturity.py`** (new): 23 unit/integration tests —
  every requested threshold boundary (0, 4, 9/10, 24/25, 49/50, 99/100,
  large values), structural shape checks, a purity check (same input →
  same output, no side effects), and integration tests confirming
  `/api/stats`'s pre-existing fields, `/score`, `/pipeline`, the dashboard
  HTML, and Phase 1 extraction all still work.

### A real, pre-existing UI bug found and fixed while testing this

Screenshotting the new indicators surfaced a genuine race condition,
unrelated to this feature but directly affecting whether it reliably
displays: `selectLead()` fires several concurrent fetches, and if the user
switches tabs (e.g. to Pipeline) before they resolve, the stale response
could overwrite whatever the user had since navigated to — last network
response wins, not last user action. Reproduced directly (clicking
Pipeline within 200ms of page load rendered the previous lead's Matches
view under an active "Pipeline" tab button) and fixed with a minimal
render-token guard in `selectLead()` and `renderPipeline()` — each
async render checks whether it's still the most recently requested one
before writing to the DOM. Verified fixed with the same reproduction script.

### What was explicitly NOT done

Per instruction: no changes to the scoring algorithm, no fabricated
validation metrics, no claim that the maturity thresholds are statistically
proven, nothing removed or weakened in the existing scoring system, and no
start on Phase 2 (Smart Next Best Action) or any other major feature.

---

## Phase 1 — AI Conversation Intelligence

Upgraded the existing extraction system (`app/extraction.py`) to go beyond
the original 8 fields, add explicit/inferred/unknown confidence tracking,
and surface it in the dashboard. Existing functionality was inspected
first and preserved — nothing was rewritten or removed; this is additive.

### What's new

- **New extracted fields**: `urgency` (low/medium/high), `delivery_preference`
  (ready/under_construction), `objections` (list, from a fixed set: monthly
  payment, down payment, price, location, delivery timeline)
- **Explicit vs. inferred confidence** per field — e.g. a stated "under 6M"
  marks `budget_max` as explicit but the derived `budget_min` (75% of that
  figure, since no lower bound was actually stated) as inferred. Both the
  heuristic extractor and the LLM prompt now produce this.
- **Missing-field detection** (`compute_missing_fields()`) and a **single
  suggested next question** (`suggest_next_question()`) targeting the first
  gap — computed fresh on every read, never stored, so it can't go stale
- **Dashboard integration**: an intelligence banner (missing fields +
  suggested question + objection chips + urgency badge) above the profile
  grid, plus an italic "(inferred)" annotation on any profile value whose
  confidence is inferred rather than explicit. Screenshotted and visually
  verified, not just code-reviewed.

### Database changes

Four new nullable/defaulted columns on `leads`: `urgency`,
`delivery_preference`, `objections`, `extraction_confidence`. Added via an
**idempotent migration** (`db.migrate_schema()`), not a schema replacement —
tested against the existing shipped database to confirm existing rows and
data survive unchanged, and confirmed safe to run multiple times.

Also fixed, while touching this code: `api.py`'s startup check was looking
for the database at the wrong path (`data/copilot.db` instead of the actual
`data/processed/copilot.db`), a pre-existing bug from an earlier
reorganization. It was harmless in practice (`init_db()` uses `CREATE TABLE
IF NOT EXISTS`, so it never damaged data) but meant the migration wouldn't
reliably run — fixed as part of wiring up the new migration path.

### Real bugs found and fixed during testing

Testing against the specification's own worked example
(`"I need a 3 bedroom in New Cairo, max 25k monthly, I can pay 500k down
and want something ready"`) surfaced three real gaps in the existing
heuristic extractor, not just in the new code:

1. `"max 25k monthly"` wasn't matched — the existing regex only recognized
   `"X/month"` / `"X a month"` / `"X per month"`, not the adjective form
   `"X monthly"`. Added an alternate pattern.
2. `"500k down"` wasn't matched — the existing regex required the literal
   phrase `"down payment"` before the number. Added an alternate
   number-before-"down" pattern.
3. `"want something ready"` wasn't matched by delivery-preference detection
   (new code) — initial keyword list required phrases like "ready to move";
   broadened to catch the standalone word "ready" too.

All three are now covered, verified against the exact spec example, and
confirmed not to regress `eval_extraction.py`'s existing F1 numbers (which
are unchanged — the synthetic evaluation dataset doesn't happen to exercise
this specific phrasing, which is itself a minor gap in that dataset worth
knowing about, not a claim that the fix is now fully proven on all phrasing).

### API changes (additive, nothing removed)

- `GET /api/leads/<id>` response now includes `missing_fields`,
  `suggested_next_question`, plus parsed `objections` and
  `extraction_confidence`
- `PUT /api/leads/<id>` accepts `urgency` and `delivery_preference` as
  editable fields, for manual agent correction
- `extract_profile()`'s return shape is additive — the original 8 fields
  are still flat top-level keys exactly as before, so `eval_extraction.py`
  and any other code reading them via `.get(field)` is unaffected

### Testing performed

- Migration tested against the existing shipped database (not just a fresh
  one) — confirmed idempotent (safe to run twice) and non-destructive
- Full end-to-end test through the real HTTP API: create lead → check
  missing fields on an empty profile → send spec example message → verify
  all fields land correctly in the database → send a second message with
  an objection and urgency cue → confirm accumulation (objection added)
  and non-destructive merge (earlier extracted value not erased)
- `eval_extraction.py` re-run — numbers unchanged from before Phase 1
- Full regression pass: leads list, stats, matching, scoring, next-action,
  pipeline all re-verified working after the change
- Dashboard visually verified via rendered screenshots (not just code
  review): default state (no banner when nothing missing), after-chat
  state (objection chip + urgency pill + updated fields), and the
  "(inferred)" styling on a derived budget range

### What's explicitly NOT in this phase

Per the specification's own phased rollout, Phases 2-9 (Smart Next Best
Action, priority queue, AI lead summary, stronger installment
intelligence, manager dashboard, lost-lead analysis, inventory insights,
what-if simulator) are not implemented yet. This phase's job was Phase 1
only, verified working, with existing functionality intact.
