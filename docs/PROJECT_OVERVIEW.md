# Project Overview: AI Real Estate Sales Copilot

*One-page framing of the project — what problem it solves, what it does,
how it was built, and what's next. Written to be reused: as a CV bullet
source, a portfolio README intro, or the basis for a pitch conversation.*

---

## The problem

Egyptian real estate brokerages (typically 5-50 agents) receive leads
through WhatsApp, Facebook ads, and phone calls, and manage them in
spreadsheets or basic CRM tools. This creates three specific, costly gaps:

1. **Agents don't know who to prioritize.** With dozens of active leads,
   there's no signal distinguishing a hot lead from a cold one beyond gut
   feel and memory.
2. **Property matching ignores how Egyptians actually buy.** Most
   primary-market sales are on installment plans (5-10 years, varying down
   payments), not cash. A "budget" filter based on sticker price alone
   routinely surfaces properties the customer can't actually afford
   month-to-month, or hides ones they can.
3. **Nothing learns from outcomes.** Which leads convert, which agents
   close fastest, and why, isn't systematically captured or fed back into
   how future leads get handled.

The market's dominant players (Nawy, Aqarmap, Property Finder Egypt) solve
a different problem — consumer-facing property search — and are
well-funded incumbents in that space. The gap identified here is
**internal sales-team tooling**, not another listings marketplace.

## What this offers

**An AI sales copilot that tells a brokerage's agents exactly who to
contact next, and why** — not a chatbot, not a search engine.

- **Installment-aware property matching** — compares a lead's real
  monthly-payment ceiling against actual plan options, not just cash price
- **Lead scoring with plain-language reasons** — a number an agent can
  trust because it comes with "why," not a black box
- **Next Best Action** — a concrete recommendation per lead (call now /
  WhatsApp today / wait) with a suggested opening message, not just a
  sorted list
- **A feedback loop** — every logged outcome (contacted, won, lost)
  measurably shifts future scoring, not just gets recorded

## The solution / how it works

The system follows one core loop — every component earns its place by
feeding this loop, and nothing gets added that doesn't:

```
Lead comes in (WhatsApp / ad / call)
        ↓
Conversation extracted into a structured profile
        ↓
Matched against inventory (installment-aware)
        ↓
Lead scored + given a Next Best Action
        ↓
Agent acts → outcome logged
        ↓
Outcome feeds back into the scoring model
```

Technically: a rule-based deterministic matching engine (explainable by
design — every score comes with reasons), a scikit-learn lead-scoring
model, and an LLM extraction layer (with a tested regex fallback for
offline/no-API-key operation) for turning raw conversation text into
structured requirements.

## How we approached solving it

The build deliberately staged real data in over synthetic data, rather
than either waiting for real data before starting or never validating
against it:

1. **Built the full 5-phase loop on synthetic data first** — properties,
   leads, and conversations — to prove the architecture before investing in
   real-data acquisition.
2. **Replaced synthetic property data with real data first**, since it was
   the one track with a legitimate public source (a Kaggle dataset of
   ~19,924 real Egyptian listings) — no privacy barrier, unlike customer
   conversations or sales outcomes.
3. **Measured, didn't assumed.** Ran the extraction engine against 100
   labeled synthetic conversations (Arabic/English/Arabizi/Mixed, matching
   real market language distribution) and reported actual computed
   precision/recall/F1 per field — including where it's weak, not just
   where it's strong.
4. **Treated real data as a source of findings, not just volume.**
   Ingesting the real property dataset surfaced actual data-quality bugs
   (e.g. listings with implausible prices) that synthetic data never would
   have — those got caught, filtered, and documented, not silently ignored.
5. **Built the privacy/compliance groundwork before, not after, approaching
   real brokerages** — a data processing policy, a DPA template, and an
   automated PII-scanning gate that a real data import must pass before
   it's allowed into the system, tested against both clean and
   deliberately bad data.

## What's actually been achieved (evidence, not claims)

- **18,010 real Egyptian property listings** ingested and matched, across
  14 real submarkets — the matching engine required zero code changes to
  work on real data
- **Real prices independently cross-checked** against market research done
  before ingestion (e.g. New Cairo apartments: 59,536 EGP/sqm real vs.
  61,550 estimated — a close match)
- **Real, measured extraction accuracy**: F1 0.91 on payment preference,
  0.75 on location, but only 0.39 on budget and 0.30 on timeline with the
  regex fallback — and a clear, evidenced finding that it's close to blind
  on Arabic-script text specifically, which is the actual argument for
  needing the LLM extraction path in this market
- **A tested PII safety gate** that correctly passes clean data and blocks
  a file containing a name, phone number, and email

## What's not real yet (say this plainly, don't blur it)

Leads, conversations, and sales outcomes are still entirely synthetic — no
real brokerage data has been collected. The lead-scoring model is trained
on synthetic rules, not real conversions. This distinction matters and
shouldn't get lost as the project gets more impressive-sounding.

## Next steps (sequenced, not a wishlist)

The bottleneck from here is not technology — it's validation against real
sales behavior. In order:

1. **Pilot readiness**: basic login/auth and brokerage-data isolation
   (currently missing — flagged explicitly in the privacy policy as a
   blocker for any multi-brokerage use)
2. **One real pilot**: 2-5 agents, 50-100 anonymized historical leads,
   2-4 weeks, measuring whether the recommendations actually match what
   experienced agents already sense — this is the single highest-leverage
   next step, above any new feature
3. **Only if the pilot shows agents want it**: WhatsApp Business API
   integration — building this before knowing agents will use the product
   would be solving the wrong problem first
4. **Real lead scoring**: once real outcomes exist, retrain the model on
   them (the retraining pipeline already exists and is tested — it just
   has no real outcomes to learn from yet)
5. **Scale considerations** (multi-tenancy, production infra) — deliberately
   last, since none of it matters if step 2 shows the core idea doesn't
   land with real agents
