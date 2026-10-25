# Brokerage Pitch

Read `README.md`'s "Honest limitations" section first, and `DATA_COLLECTION.md`
for what happens to the data after you get it — this document is just the
outreach/ask itself.

## The pitch (don't ask for "all your data")

Use this as a starting message, not a script — adapt it to whoever you're
talking to. Short, concrete, low-commitment ask first.

> Hi [name], I've been building an AI tool that helps real estate sales
> teams prioritize leads and match customers to units based on their actual
> payment ability (cash vs. installment plans), not just listed price. I'd
> like to test it against a small batch of your past leads — fully
> anonymized, no names or phone numbers needed — to see if the
> prioritization actually lines up with what your agents already know.
> Would you be open to sharing 50-100 anonymized historical leads
> (conversation summary, requirements, and outcome) so I can validate it?
> Happy to show you the tool first.

Key things this does on purpose:
- Leads with a demo, not a data request, as the opener
- Names a small, low-risk number (50-100) instead of "all your data"
- Explicitly says no PII needed, up front
- Frames it as validation ("does this match what your agents already know"),
  which is something a sales manager can actually evaluate and say yes to

## What to actually request

Send `data_request_template.csv` (in this folder) directly — it's the exact
schema `scripts/ingest_brokerage_data.py` expects. Walk them through it
rather than just emailing it cold; the `conversation_summary` and
`final_outcome` columns are usually the ones that need the most explanation.

Lead with `docs/pilot_pitch.docx` — a one-page pitch document designed to
be sent or handed to a brokerage owner/manager, not read internally. Fill
in the `[your name]` / `[phone/WhatsApp]` / `[email]` placeholders before
sending it — it's a template, not a finished document.

Explicitly tell them **not** to include: names, phone numbers, emails,
national IDs, exact addresses. If they're unsure how to strip these, offer
to review the export with them before you take a copy — and remember
`scripts/validate_data.py` will refuse to let you ingest it if it still
contains any of these, as a second safety net.

## Realistic expectations

- Most small brokerages will say no or go quiet — that's normal, not a
  signal the idea is bad. The roadmap suggests contacting 10-20; expect
  most of that list to be silence.
- The ones who say yes will likely give you messy, inconsistent data.
  Budget time for cleaning it (`validate_data.py` will tell you what's
  wrong, not fix it for you).
- 50-100 leads is not enough to train a model from scratch — it's enough to
  validate that the *synthetic-trained* model's rankings are directionally
  sane against real cases, and to seed the feedback loop described in
  Phase 5. Don't claim more than that from a first batch.
- If someone offers a much larger dataset (hundreds+) unprompted, treat
  that as a reason to slow down and ask how it was collected/consented, not
  as pure upside.

## Before you contact anyone, have these ready

Per the roadmap, don't start outreach until you can point to all of these:

- [x] `docs/data_request_template.csv` — done
- [x] `docs/DATA_DICTIONARY.md` — done
- [x] `docs/DATA_COLLECTION.md` — done
- [x] `scripts/ingest_brokerage_data.py` (+ `validate_data.py` gate) — done
- [x] A working demo on real property data (18,010 real Egyptian listings) — done
- [x] `docs/DATA_PROCESSING_POLICY.md` — done, draft only, needs legal review before real use
- [x] `docs/DATA_PROCESSING_AGREEMENT.md` — done, template only, needs legal review before signing
- [x] `docs/pilot_pitch.docx` — one-page pilot pitch, ready to send (fill in your contact details first)

All eight exist in this repo as of this build — you're ready to start
Track C (see `DATA_COLLECTION.md`) whenever you want. The two legal
documents are explicitly marked as drafts requiring local legal review —
don't present them to a brokerage as finished legal agreements.
