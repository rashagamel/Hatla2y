"""
Ingests a real, anonymized brokerage CSV export (matching
docs/data_request_template.csv's schema) into leads / lead_events / outcomes.

Mandatory validation gate: this script REFUSES to ingest anything that fails
validate_data.py's PII/schema check. There is no --skip-validation flag on
purpose — if the data doesn't pass, fix the export, don't bypass the check.

Every ingested lead is tagged data_source='real' (leads.data_source column)
so scoring/reporting can always distinguish real signal from the synthetic
baseline — see feedback.py's retrain_from_outcomes(), which already weights
real outcomes more heavily than synthetic ones.

Usage:
    python3 ingest_brokerage_data.py path/to/broker_export.csv
"""
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from db import get_connection

from validate_data import validate

OUTCOME_MAP = {
    "won": "won", "lost": "lost", "no_response": "no_response",
    "still_active": None,  # no outcome to log yet — lead stays open
}


def parse_num(val):
    if val in (None, "", "NULL"):
        return None
    try:
        return float(val)
    except ValueError:
        return None


def parse_bool(val):
    return str(val).strip().lower() in ("yes", "true", "1", "y")


def ingest(csv_path):
    print(f"Validating {csv_path} before ingestion...\n")
    if not validate(csv_path):
        print("\nRefusing to ingest — fix the validation errors above first.")
        sys.exit(1)

    print("\nValidation passed. Ingesting...\n")
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    conn = get_connection()
    inserted = 0
    for row in rows:
        # Note: name is intentionally NOT collected — leads.name is required NOT NULL
        # in the schema, so we use the anonymized lead_id as a placeholder label. Do not
        # backfill this with a real name even if you have it separately.
        cur = conn.execute("""
            INSERT INTO leads
            (name, channel, data_source, budget_min, budget_max, payment_preference,
             max_monthly_payment, max_down_payment, preferred_locations, min_bedrooms,
             purpose, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            f"Lead {row.get('lead_id', '?')}", row.get("channel") or "whatsapp", "real",
            parse_num(row.get("stated_budget_min")), parse_num(row.get("stated_budget_max")),
            row.get("payment_preference") or "unknown",
            parse_num(row.get("stated_monthly_max")), parse_num(row.get("stated_down_payment_max")),
            row.get("preferred_locations") or "", parse_num(row.get("min_bedrooms")),
            row.get("purpose") or "unknown",
            "won" if row.get("final_outcome") == "won" else
            "lost" if row.get("final_outcome") == "lost" else "new",
        ))
        lead_id = cur.lastrowid

        if row.get("conversation_summary"):
            conn.execute(
                "INSERT INTO lead_events (lead_id, event_type, payload) VALUES (?, 'message_in', ?)",
                (lead_id, row["conversation_summary"]),
            )
        if parse_bool(row.get("viewing_requested")):
            conn.execute(
                "INSERT INTO lead_events (lead_id, event_type, payload) VALUES (?, 'viewing_booked', 'From real data import')",
                (lead_id,),
            )
        properties_shown = int(parse_num(row.get("properties_shown")) or 0)
        for _ in range(properties_shown):
            conn.execute(
                "INSERT INTO lead_events (lead_id, event_type, payload) VALUES (?, 'property_viewed', 'From real data import')",
                (lead_id,),
            )

        outcome = OUTCOME_MAP.get(row.get("final_outcome"), row.get("final_outcome"))
        if outcome:
            conn.execute(
                "INSERT INTO outcomes (lead_id, outcome, note) VALUES (?,?,?)",
                (lead_id, outcome, "Imported from real brokerage export"),
            )
        inserted += 1

    conn.commit()
    conn.close()
    print(f"Ingested {inserted} real leads (tagged data_source='real').")
    print("Next: run `python3 feedback.py` or call retrain_from_outcomes() to blend "
          "these real outcomes into the scoring model.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 ingest_brokerage_data.py path/to/broker_export.csv")
        sys.exit(1)
    ingest(sys.argv[1])
