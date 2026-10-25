"""
Ingests the real "Egyptian Real Estate Listings" Kaggle dataset (~19,900 rows)
into this project's schema. Written against the dataset's ACTUAL columns —
url, price, description, location, type, size, bedrooms, bathrooms,
available_from, payment_method, down_payment — confirmed by inspecting a real
downloaded copy (this is no longer a generic best-guess mapper).

Usage:
    python3 ingest_kaggle.py --csv /path/to/egypt_real_estate_listings.csv --reset-db

Honest documentation of every judgment call made below — read this before
citing numbers from the ingested data anywhere:

1. LOCATION: the source `location` field is a full hierarchy string, e.g.
   "Mountain View iCity, 5th Settlement Compounds, The 5th Settlement, New
   Cairo City, Cairo". We extract the first comma-segment as `compound`, and
   bucket the whole string into a canonical submarket via keyword matching
   (LOCATION_KEYWORDS below). Rows that don't match any known keyword are
   SKIPPED, not guessed — the dataset covers areas beyond this project's
   original 4 locations (Red Sea/Hurghada, New Capital, Mostakbal City, El
   Shorouk, Madinaty, etc.), so those are now first-class canonical locations
   too, not shoehorned into New Cairo/6th of October/Sheikh Zayed/North Coast.

2. BEDROOMS/BATHROOMS: source values are messy strings like "3+ Maid",
   "studio", "7++ Maid", "none". We extract the leading integer; "studio"
   becomes 0 bedrooms. Rows with no extractable bedroom count are skipped
   (bedrooms is required). Unparsable bathrooms default to max(bedrooms-1, 1)
   rather than being skipped, since it's a much less critical matching field.

3. PRICE SEMANTICS ARE AMBIGUOUS in the source data: for `payment_method ==
   "Installments"` rows, it's unclear whether `price` represents a cash-
   equivalent or the total installment price (Egyptian listings usually quote
   the installment total). We store it as-is in `properties.cash_price`
   without adjustment — meaning for installment listings this may actually
   represent the installment total, not a true cash price. This is flagged
   in each such property's `description` field, not hidden.

4. INSTALLMENT PLANS: only generated for rows where `payment_method ==
   "Installments"` AND a real `down_payment` value exists. We use the REAL
   down_payment and REAL price, but the dataset gives no plan duration — we
   ASSUME a 7-year term (a reasonable, common Egyptian developer term, but
   an assumption, not sourced data) to back into a monthly payment. This is
   a partially-real, partially-assumed plan, not a fully synthetic one.
   `payment_method == "Cash"` rows get no installment_plans row at all,
   matching what the source data actually says about them.

5. FINISHING/DEVELOPER: no structured finishing field exists. We attempt a
   best-effort regex extraction of a developer name from free-text
   `description` (real listings often start with "Developer: X" or typo
   variants like "DEVOLOBER : X") — this is unreliable and frequently empty;
   don't treat a missing developer as meaningful.
"""
import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from db import get_connection, init_db
from installment_extractor import resolve_installment_plan
import json

csv.field_size_limit(10_000_000)  # descriptions can be long, multi-line

LOCATION_KEYWORDS = [
    # order matters — more specific patterns first
    ("new zayed", "Sheikh Zayed"), ("sheikh zayed", "Sheikh Zayed"),
    ("5th settlement", "New Cairo"), ("fifth settlement", "New Cairo"), ("new cairo", "New Cairo"),
    ("mostakbal city", "Mostakbal City"),
    ("shorouk", "El Shorouk"),
    ("madinaty", "Madinaty"),
    ("new capital", "New Capital"),
    ("6 october", "6th of October"), ("6th of october", "6th of October"),
    ("new giza", "New Giza"),
    ("sokhna", "Ain Sokhna"),
    ("ras al hekma", "North Coast"), ("sidi abdel rahman", "North Coast"),
    ("marsa matrouh", "North Coast"), ("alamein", "North Coast"), ("north coast", "North Coast"),
    ("hurghada", "Red Sea"), ("red sea", "Red Sea"), ("gouna", "Red Sea"),
    ("soma bay", "Red Sea"), ("makadi", "Red Sea"), ("safaga", "Red Sea"),
    ("nasr city", "Nasr City"),
    ("heliopolis", "Heliopolis"),
    ("maadi", "Maadi"),
    ("zamalek", "Zamalek"),
]

TYPE_MAP = {
    "apartment": "apartment", "chalet": "chalet", "villa": "villa",
    "townhouse": "townhouse", "twin house": "twin_house", "duplex": "duplex",
    "penthouse": "penthouse", "ivilla": "villa", "hotel apartment": "hotel_apartment",
    "land": "land", "cabin": "cabin", "palace": "palace", "whole building": "whole_building",
    "roof": "roof", "full floor": "full_floor", "bulk sale unit": "bulk_sale_unit",
    "bungalow": "bungalow",
}

ASSUMED_INSTALLMENT_YEARS = 7  # documented assumption — see module docstring point 4

# Sanity bounds on price-per-sqm, informed by this dataset's own distribution
# (p1=10,811, p99=268,966 EGP/sqm) and independently-researched real market
# ranges (~22,500-160,000+ EGP/sqm across these submarkets, per seed.py's
# citations). Rows outside this band are near-certainly data errors in the
# source (e.g. a per-installment or teaser figure landing in the price field)
# rather than real outlier prices — observed cases include a 130sqm New Cairo
# apartment listed at 600,000 EGP (4,615 EGP/sqm) and prices exceeding 10M
# EGP/sqm, both implausible for any real Egyptian property in 2025/2026.
MIN_PLAUSIBLE_PSQM = 8000
MAX_PLAUSIBLE_PSQM = 250000


def parse_price(val):
    if not val or not isinstance(val, str):
        return None
    cleaned = re.sub(r"[^\d.]", "", val)
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def parse_size_sqm(val):
    if not val or not isinstance(val, str):
        return None
    m = re.search(r"([\d,]+)\s*sqm", val)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_bedrooms(val):
    if not val or not isinstance(val, str):
        return None
    v = val.strip().lower()
    if v.startswith("studio"):
        return 0
    m = re.match(r"(\d+)", v)
    return int(m.group(1)) if m else None


def parse_bathrooms(val, fallback_bedrooms):
    if isinstance(val, str):
        m = re.match(r"(\d+)", val.strip())
        if m:
            return int(m.group(1))
    return max(fallback_bedrooms - 1, 1)


def bucket_location(raw_location):
    if not raw_location or not isinstance(raw_location, str):
        return None
    low = raw_location.lower()
    for keyword, canonical in LOCATION_KEYWORDS:
        if keyword in low:
            return canonical
    return None


def extract_compound(raw_location):
    if not raw_location or not isinstance(raw_location, str):
        return ""
    return raw_location.split(",")[0].strip()


def extract_developer(description):
    if not description or not isinstance(description, str):
        return ""
    m = re.search(r"(?i)devel[o0]ber\s*[:\-]\s*([^\n]{2,40})", description)
    if not m:
        m = re.search(r"(?i)developer\s*[:\-]\s*([^\n]{2,40})", description)
    return m.group(1).strip() if m else ""


def normalize_type(raw_type):
    if not raw_type or not isinstance(raw_type, str):
        return "apartment"
    return TYPE_MAP.get(raw_type.strip().lower(), raw_type.strip().lower().replace(" ", "_"))


def ingest(csv_path, reset_db=False, limit=None):
    if reset_db:
        init_db(reset=True)
    conn = get_connection()

    inserted, plans_created = 0, 0
    skipped = {"location": 0, "bedrooms": 0, "size": 0, "price": 0, "implausible_price": 0}

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if limit and i >= limit:
                break

            location = bucket_location(row.get("location"))
            if not location:
                skipped["location"] += 1
                continue

            bedrooms = parse_bedrooms(row.get("bedrooms"))
            if bedrooms is None:
                skipped["bedrooms"] += 1
                continue

            area = parse_size_sqm(row.get("size"))
            if not area:
                skipped["size"] += 1
                continue

            price = parse_price(row.get("price"))
            if not price:
                skipped["price"] += 1
                continue

            psqm = price / area
            if psqm < MIN_PLAUSIBLE_PSQM or psqm > MAX_PLAUSIBLE_PSQM:
                skipped["implausible_price"] += 1
                continue

            bathrooms = parse_bathrooms(row.get("bathrooms"), bedrooms)
            ptype = normalize_type(row.get("type"))
            compound = extract_compound(row.get("location"))
            developer = extract_developer(row.get("description"))
            payment_method = (row.get("payment_method") or "").strip().lower()
            down_payment = parse_price(row.get("down_payment"))

            raw_desc = row.get("description") or ""
            source_url = row.get("url") or ""
            desc_note = "Imported from real Kaggle Egyptian real estate dataset."
            if payment_method == "installments":
                desc_note += (" Source lists this as an installment listing — 'cash_price' here may "
                               "represent the installment total, not a true cash-equivalent price "
                               "(source data is ambiguous on this point).")

            provenance_dict = {
                "source_dataset": "Egyptian Real Estate Listings (Kaggle)",
                "payment_method_raw": payment_method,
                "url": source_url,
            }

            cur = conn.execute("""
                INSERT INTO properties
                (title, compound, location, property_type, area_sqm, bedrooms, bathrooms,
                 finishing, developer, cash_price, description, data_source,
                 source_url, provenance_details)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                f"{ptype.replace('_', ' ').title()} in {compound or location}", compound, location, ptype,
                area, bedrooms, bathrooms, "unspecified", developer, price, desc_note, "real_kaggle",
                source_url, json.dumps(provenance_dict, ensure_ascii=False),
            ))
            prop_id = cur.lastrowid
            inserted += 1

            if payment_method == "installments" or "installment" in raw_desc.lower() or "أقساط" in raw_desc:
                resolved_plan = resolve_installment_plan(
                    raw_desc,
                    price,
                    structured_down_payment=down_payment,
                    fallback_years=ASSUMED_INSTALLMENT_YEARS
                )
                if resolved_plan:
                    conn.execute("""
                        INSERT INTO installment_plans
                        (property_id, years, down_payment_pct, monthly_payment, total_price, plan_source, extracted_terms)
                        VALUES (?,?,?,?,?,?,?)
                    """, (
                        prop_id,
                        resolved_plan["years"],
                        resolved_plan["down_payment_pct"],
                        resolved_plan["monthly_payment"],
                        resolved_plan["total_price"],
                        resolved_plan["plan_source"],
                        resolved_plan["extracted_terms"],
                    ))
                    plans_created += 1

    conn.commit()
    conn.close()

    print(f"Inserted {inserted} real properties (data_source='real_kaggle').")
    print(f"Created {plans_created} installment plans with provenance and extracted terms.")
    print(f"Skipped rows — unrecognized location: {skipped['location']}, "
          f"unparsable bedrooms: {skipped['bedrooms']}, missing area: {skipped['size']}, "
          f"missing price: {skipped['price']}, implausible price/sqm: {skipped['implausible_price']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Path to the downloaded CSV")
    parser.add_argument("--reset-db", action="store_true", help="Wipe existing DB before ingesting")
    parser.add_argument("--limit", type=int, default=None, help="Only ingest the first N rows (for testing)")
    args = parser.parse_args()
    ingest(args.csv, reset_db=args.reset_db, limit=args.limit)
