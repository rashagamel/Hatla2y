# Data Processing Policy

**Status: draft, prepared ahead of any real data collection. This has not
been reviewed by an Egyptian lawyer and should not be treated as a
compliant legal document until it is.** Egypt's Personal Data Protection
Law (Law 151/2020) governs the collection and processing of personal data
in Egypt, including customer names, phone numbers, and conversation
content — anything in this document that touches real leads should be
reviewed against it (and ideally by a local lawyer) before being relied on
in an actual pilot.

This document exists so that a brokerage considering a pilot has a clear,
concrete answer to "what happens to our data" before they say yes — not as
a substitute for that legal review.

## What data this system collects

From a participating brokerage, at minimum:

- Anonymized lead identifiers (internal reference only — never names, phone
  numbers, emails, or national IDs; see "What never enters the system" below)
- Conversation summaries or de-identified message content
- Stated requirements: budget, location preference, bedrooms, payment
  preference, timeline, purpose
- Interaction history: response times, properties viewed, viewings
  requested/attended
- Sales outcomes: contacted, viewing booked, negotiating, won, lost, no response

From public/licensed sources, separately: property listing data (see
`DATA_COLLECTION.md` for how the current real property dataset was sourced).

## Why this data is collected

Solely to power the system's core functions: matching leads to properties,
scoring lead priority, generating next-action recommendations, and
measuring whether those recommendations actually help close more deals.
No data collected here is used for any purpose beyond operating and
improving this system for the brokerage that provided it.

## What never enters the system

The following are excluded by design, not just by policy — see
`scripts/validate_data.py`, which scans every incoming data file for these
patterns and refuses to let them through:

- Customer names
- Phone numbers
- Email addresses
- National ID numbers
- Exact home/work addresses

If any of these are found in an import, the import is rejected until the
brokerage strips them, not silently cleaned or worked around.

## Who can access the data

During a pilot: the brokerage's own sales agents/managers (their own
leads only) and whoever is operating this system on their behalf. There is
currently no multi-tenant isolation built — see the "Honest limitations"
section of the main `README.md`. **A pilot involving more than one
brokerage should not proceed until that isolation is actually implemented
and tested**, not assumed.

## Retention

Default position for a pilot: data is retained only for the duration of
the pilot plus a short review period (suggest 30 days) afterward, then
deleted, unless the brokerage explicitly agrees to longer retention in the
Data Processing Agreement. This default should be stated explicitly in
every pilot conversation, not left implicit.

## Deletion

On request from the brokerage, at any time, all of their data (leads,
conversations, outcomes, and any derived scores/matches referencing them)
is deleted from `data/processed/copilot.db` and any backups within a stated
window (suggest 7 days). This should be tested before a real pilot, not
assumed to work — write and run an actual deletion script against test
data first.

## Anonymization expectations

The brokerage is asked to strip direct identifiers (name, phone, email,
national ID, exact address) before sharing data — see
`docs/data_request_template.csv` for the exact schema requested and
`docs/BROKERAGE_PITCH.md` for how this is communicated to them.
`scripts/validate_data.py` provides a second, automated check rather than
relying on the brokerage getting this right unassisted. Note that
anonymization is not the same as re-identification-proof — a small dataset
with unusual combinations of attributes (e.g. an unusually high budget in a
specific niche compound) could theoretically be re-identifiable by someone
who already knows the brokerage's client list. This is a real limitation
of small-scale anonymization, not fully solved by removing direct
identifiers alone, and should be disclosed honestly rather than overstated
as "fully anonymous."

## Real-data provenance tracking

Every record's origin is tracked in the database itself (`data_source`
column — see `docs/DATA_DICTIONARY.md`) so real brokerage data is never
silently mixed with or mistaken for synthetic demo data.
