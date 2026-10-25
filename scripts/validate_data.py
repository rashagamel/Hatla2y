"""
Validates an incoming brokerage CSV export BEFORE it touches the database:
  1. Checks columns roughly match data_request_template.csv's schema
  2. Scans cell values for likely PII (phone numbers, emails, national ID-shaped
     numbers, and column headers that suggest names) and refuses to proceed if
     found, rather than silently ingesting it

This is the concrete implementation of "do not request/store PII" — a written
policy is only as good as the check that enforces it. Run this on anything a
brokerage sends you, even if they said it's already anonymized — people make
copy-paste mistakes.

Usage:
    python3 validate_data.py path/to/broker_export.csv
    python3 validate_data.py path/to/broker_export.csv --strict   # exit non-zero on any warning
"""
import csv
import re
import sys
import os

EXPECTED_COLUMNS = [
    "lead_id", "conversation_summary", "stated_budget_min", "stated_budget_max",
    "payment_preference", "stated_monthly_max", "stated_down_payment_max",
    "preferred_locations", "min_bedrooms", "purpose", "channel",
    "first_response_time_hours", "properties_shown", "viewing_requested",
    "viewing_attended", "final_outcome", "days_to_outcome",
]

# Column names that strongly suggest PII was included despite instructions
SUSPICIOUS_COLUMN_NAMES = [
    "name", "customer_name", "full_name", "phone", "mobile", "email",
    "national_id", "id_number", "address", "whatsapp_number",
]

PHONE_RE = re.compile(r"(?:\+?20|0)?1[0-25]\d{8}\b")  # Egyptian mobile pattern
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
NATIONAL_ID_RE = re.compile(r"\b[23]\d{13}\b")  # Egyptian national ID: 14 digits, starts 2 or 3


def validate(csv_path, strict=False):
    errors, warnings = [], []

    if not os.path.exists(csv_path):
        print(f"ERROR: file not found: {csv_path}")
        return False

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        rows = list(reader)

    # --- schema check ---
    missing = [c for c in EXPECTED_COLUMNS if c not in header]
    extra = [c for c in header if c not in EXPECTED_COLUMNS]
    if missing:
        warnings.append(f"Missing expected columns: {missing}")
    if extra:
        warnings.append(f"Unexpected extra columns (review these manually): {extra}")

    # --- column-name PII check ---
    for col in header:
        col_norm = col.strip().lower().replace(" ", "_")
        for suspicious in SUSPICIOUS_COLUMN_NAMES:
            if suspicious in col_norm:
                errors.append(f"Column '{col}' looks like it may contain PII (matched '{suspicious}')")

    # --- cell-value PII scan (checks every cell, not just suspicious columns —
    #     PII often ends up in free-text fields like conversation_summary) ---
    pii_hits = {"phone": 0, "email": 0, "national_id": 0}
    for i, row in enumerate(rows):
        for col, val in row.items():
            if not val:
                continue
            if PHONE_RE.search(val):
                pii_hits["phone"] += 1
            if EMAIL_RE.search(val):
                pii_hits["email"] += 1
            if NATIONAL_ID_RE.search(val):
                pii_hits["national_id"] += 1

    for kind, count in pii_hits.items():
        if count > 0:
            errors.append(f"Found {count} cell(s) matching {kind}-like patterns — review and strip before ingesting")

    # --- report ---
    print(f"Validated {csv_path}: {len(rows)} rows, {len(header)} columns\n")
    if errors:
        print("ERRORS (must fix before ingesting):")
        for e in errors:
            print(f"  ✗ {e}")
    if warnings:
        print("\nWARNINGS (review, may be fine):")
        for w in warnings:
            print(f"  ⚠ {w}")
    if not errors and not warnings:
        print("✓ No issues found. Schema matches, no PII patterns detected.")

    passed = len(errors) == 0 and (not strict or len(warnings) == 0)
    print(f"\n{'PASS' if passed else 'FAIL'} — {'safe to ingest' if passed else 'do not ingest yet'}")
    return passed


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 validate_data.py path/to/export.csv [--strict]")
        sys.exit(1)
    strict = "--strict" in sys.argv
    ok = validate(sys.argv[1], strict=strict)
    sys.exit(0 if ok else 1)
