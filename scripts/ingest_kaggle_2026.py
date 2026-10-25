"""
Ingests the "Egypt Real Estate Data 2026" Kaggle dataset (~40k rows)
into this project's schema. This is the most current and richest
Egyptian PropertyFinder dataset available as of mid-2026.

Source: https://www.kaggle.com/datasets/hossamelgendy/egypt-real-estate-data-2026
Columns (51 total, key ones used here):
  Price, City, Town, District, Compound, Property_Type,
  Bedrooms, Bathrooms, Area, Furnished,
  Latitude, Longitude, Amenities, Payment_Method, Down_Payment,
  Description, Available_From, URL, Agent_Name, Agency_Name

Usage:
    py scripts/ingest_kaggle_2026.py --csv data/real/<file>.csv [--reset-db] [--limit N]

Judgment calls documented (please read before citing numbers):

1. LOCATION BUCKETING: Hierarchical location (City → Town → District → Compound)
   is collapsed to our canonical submarket list via keyword matching on all four
   levels (most specific first). Listings that match no known keyword are SKIPPED.
   Both sale and rental rows are included — rental rows are tagged with a note
   in their description and get no installment plan even if payment_method says
   'Installments' (rental installments are monthly rent, not a purchase plan).

2. AREA: Column may be in sqm or sqft depending on the listing source. We detect
   the unit from a suffix ("sqm", "sqft", "m²", "ft²") or from the raw magnitude
   (values > 1000 but < 15000 are treated as sqft and converted; values > 15000
   are likely data errors and are skipped along with area=0 rows). When no unit
   is present and the value is ambiguous, we treat it as sqm.

3. PRICE: EGP for sale listings. We apply the same price/sqm sanity band as
   ingest_kaggle.py (8,000–250,000 EGP/sqm). For rental listings the price
   column contains annual rent — we store it as cash_price with a note that
   it's annual rent, NOT a sale price. Do not use cash_price to compare
   sale and rental listings directly.

4. INSTALLMENT PLANS: Same logic as ingest_kaggle.py — only generated when
   payment_method == 'Installments' AND a real down_payment exists AND it's a
   SALE listing. The 7-year term assumption is retained (documented below).

5. FURNISHED/FINISHING: Mapped from the Furnished column (yes → 'fully-finished',
   no → 'core', partial → 'semi-finished'). This is imperfect — furnished ≠
   finished, but it's the best single-column proxy available.

6. GPS COORDINATES: Stored in description as a note (lat/lon) since the properties
   table has no coordinate columns. Future schema migration could add these.
"""
import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from db import get_connection, init_db
from installment_extractor import resolve_installment_plan, extract_installment_terms
import json

csv.field_size_limit(10_000_000)

# ─────────────────────────── CONSTANTS ─────────────────────────────────────

ASSUMED_INSTALLMENT_YEARS = 7  # same assumption as ingest_kaggle.py — see point 4 above

MIN_PLAUSIBLE_PSQM = 8_000
MAX_PLAUSIBLE_PSQM = 250_000

LOCATION_KEYWORDS = [
    # More-specific patterns first
    ("new zayed",       "Sheikh Zayed"),
    ("sheikh zayed",    "Sheikh Zayed"),
    ("5th settlement",  "New Cairo"),
    ("fifth settlement","New Cairo"),
    ("new cairo",       "New Cairo"),
    ("mostakbal",       "Mostakbal City"),
    ("shorouk",         "El Shorouk"),
    ("madinaty",        "Madinaty"),
    ("new capital",     "New Capital"),
    ("new administrative capital", "New Capital"),
    ("6 october",       "6th of October"),
    ("6th of october",  "6th of October"),
    ("october city",    "6th of October"),
    ("new giza",        "New Giza"),
    ("sokhna",          "Ain Sokhna"),
    ("ras al hekma",    "North Coast"),
    ("sidi abdel rahman","North Coast"),
    ("marsa matrouh",   "North Coast"),
    ("alamein",         "North Coast"),
    ("north coast",     "North Coast"),
    ("hurghada",        "Red Sea"),
    ("red sea",         "Red Sea"),
    ("el gouna",        "Red Sea"),
    ("gouna",           "Red Sea"),
    ("soma bay",        "Red Sea"),
    ("makadi",          "Red Sea"),
    ("safaga",          "Red Sea"),
    ("nasr city",       "Nasr City"),
    ("heliopolis",      "Heliopolis"),
    ("maadi",           "Maadi"),
    ("zamalek",         "Zamalek"),
    ("dokki",           "Giza"),
    ("mohandessin",     "Giza"),
    ("giza",            "Giza"),
    ("alexandria",      "Alexandria"),
    ("alex",            "Alexandria"),
]

TYPE_MAP = {
    "apartment": "apartment", "flat": "apartment", "chalet": "chalet", "villa": "villa",
    "townhouse": "townhouse", "town house": "townhouse",
    "twin house": "twin_house", "twin-house": "twin_house",
    "duplex": "duplex", "penthouse": "penthouse",
    "hotel apartment": "hotel_apartment", "serviced apartment": "hotel_apartment",
    "land": "land", "plot": "land", "cabin": "cabin",
    "palace": "palace", "whole building": "whole_building",
    "roof": "roof", "full floor": "full_floor",
    "bungalow": "bungalow", "studio": "apartment",
}

FURNISHED_TO_FINISHING = {
    "yes": "fully-finished", "furnished": "fully-finished",
    "no": "core", "unfurnished": "core",
    "partial": "semi-finished", "semi": "semi-finished",
}


# ─────────────────────────── PARSERS ────────────────────────────────────────

def parse_price(val):
    if not val or not isinstance(val, str):
        try:
            return float(val) if val else None
        except (ValueError, TypeError):
            return None
    cleaned = re.sub(r"[^\d.]", "", val)
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def parse_area_sqm(val):
    """Parse area, handling sqm/sqft/m² suffixes and unit-ambiguous raw values."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        area = float(val)
    else:
        val = str(val).strip()
        low = val.lower()
        # explicit sqft
        if "sqft" in low or "ft²" in low or "ft2" in low:
            m = re.search(r"[\d,]+\.?\d*", val)
            if not m:
                return None
            try:
                return float(m.group().replace(",", "")) * 0.0929
            except ValueError:
                return None
        # explicit sqm
        m = re.search(r"[\d,]+\.?\d*", val)
        if not m:
            return None
        try:
            area = float(m.group().replace(",", ""))
        except ValueError:
            return None

    if area <= 0 or area > 15_000:
        return None
    # heuristic: if > 1000 and looks like sqft (typical apt is 100-500 sqm, 1000-5000 sqft)
    if area > 1000:
        area = area * 0.0929  # convert sqft → sqm
    return round(area, 1)


def parse_bedrooms(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    v = str(val).strip().lower()
    if v.startswith("studio") or v == "0":
        return 0
    m = re.match(r"(\d+)", v)
    return int(m.group(1)) if m else None


def parse_bathrooms(val, fallback_bedrooms):
    if val is not None:
        if isinstance(val, (int, float)):
            return max(1, int(val))
        m = re.match(r"(\d+)", str(val).strip())
        if m:
            return max(1, int(m.group(1)))
    return max((fallback_bedrooms or 1) - 1, 1)


def bucket_location(city="", town="", district="", compound=""):
    """Try location fields from most→least specific."""
    for field in [compound, district, town, city]:
        if not field or not isinstance(field, str):
            continue
        low = field.lower()
        for keyword, canonical in LOCATION_KEYWORDS:
            if keyword in low:
                return canonical
    return None


def normalize_type(raw):
    if not raw or not isinstance(raw, str):
        return "apartment"
    return TYPE_MAP.get(raw.strip().lower(), raw.strip().lower().replace(" ", "_"))


def normalize_finishing(raw):
    if not raw or not isinstance(raw, str):
        return "unspecified"
    return FURNISHED_TO_FINISHING.get(raw.strip().lower(), "unspecified")


# ─────────────────────────── COLUMN DETECTION ───────────────────────────────

def detect_columns(fieldnames):
    """
    The 2026 dataset has 51 columns with title-cased names. This function
    maps our internal names to whatever the CSV actually calls them, to
    survive minor column name changes across dataset versions.
    """
    fn_lower = {f.lower().strip(): f for f in fieldnames}
    mapping = {}
    candidates = {
        "price":         ["price", "price_egp", "price (egp)"],
        "city":          ["city", "governorate"],
        "town":          ["town", "area", "neighborhood"],
        "district":      ["district", "sub_district", "subdistrict"],
        "compound":      ["compound", "compound_name"],
        "property_type": ["property_type", "type", "propertytype"],
        "bedrooms":      ["bedrooms", "bedroom", "beds", "no_bedrooms"],
        "bathrooms":     ["bathrooms", "bathroom", "baths", "no_bathrooms"],
        "area":          ["area", "size", "area_sqm", "size_sqm"],
        "furnished":     ["furnished", "finishing", "finish"],
        "payment_method":["payment_method", "paymentmethod", "payment"],
        "down_payment":  ["down_payment", "downpayment", "dp"],
        "description":   ["description", "desc", "details"],
        "latitude":      ["latitude", "lat"],
        "longitude":     ["longitude", "lon", "lng"],
        "listing_type":  ["listing_type", "type_listing", "sale_rent", "for"],
        "available_from":["available_from", "availablefrom", "delivery_date"],
        "agency":        ["agency_name", "agency", "developer", "developer_name"],
        "url":           ["url", "link"],
    }
    for internal, options in candidates.items():
        for opt in options:
            if opt in fn_lower:
                mapping[internal] = fn_lower[opt]
                break
    return mapping


# ─────────────────────────── MAIN INGEST ────────────────────────────────────

def ingest(csv_path, reset_db=False, limit=None, sale_only=False):
    if reset_db:
        init_db(reset=True)
    conn = get_connection()

    inserted = 0
    plans_created = 0
    skipped = {
        "location": 0, "bedrooms": 0, "area": 0, "price": 0,
        "implausible_price": 0, "rental_excluded": 0,
    }

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        col = detect_columns(reader.fieldnames or [])

        def g(row, key, default=""):
            """Safe get with column mapping."""
            mapped = col.get(key)
            if not mapped:
                return default
            return row.get(mapped, default) or default

        for i, row in enumerate(reader):
            if limit and i >= limit:
                break

            # ── Listing type ─────────────────────────────────────────────
            listing_type = g(row, "listing_type", "sale").strip().lower()
            is_rental = listing_type in ("rent", "rental", "for rent")
            if sale_only and is_rental:
                skipped["rental_excluded"] += 1
                continue

            # ── Location ─────────────────────────────────────────────────
            location = bucket_location(
                city=g(row, "city"), town=g(row, "town"),
                district=g(row, "district"), compound=g(row, "compound"),
            )
            if not location:
                skipped["location"] += 1
                continue

            compound_name = g(row, "compound") or g(row, "district") or g(row, "town") or ""

            # ── Bedrooms ─────────────────────────────────────────────────
            bedrooms = parse_bedrooms(g(row, "bedrooms"))
            if bedrooms is None:
                skipped["bedrooms"] += 1
                continue

            # ── Area ─────────────────────────────────────────────────────
            area = parse_area_sqm(g(row, "area"))
            if not area:
                skipped["area"] += 1
                continue

            # ── Price ────────────────────────────────────────────────────
            price = parse_price(g(row, "price"))
            if not price or price <= 0:
                skipped["price"] += 1
                continue

            # Price/sqm sanity (skip for rentals — rent/sqm is a different scale)
            if not is_rental:
                psqm = price / area
                if psqm < MIN_PLAUSIBLE_PSQM or psqm > MAX_PLAUSIBLE_PSQM:
                    skipped["implausible_price"] += 1
                    continue

            # ── Other fields ─────────────────────────────────────────────
            bathrooms = parse_bathrooms(g(row, "bathrooms"), bedrooms)
            ptype = normalize_type(g(row, "property_type"))
            finishing = normalize_finishing(g(row, "furnished"))
            payment_method = g(row, "payment_method", "").strip().lower()
            down_payment = parse_price(g(row, "down_payment"))

            lat = g(row, "latitude")
            lon = g(row, "longitude")
            agency = g(row, "agency")
            raw_desc = g(row, "description", "")
            source_url = g(row, "url", "")

            # Build description with provenance note
            desc_parts = [f"Imported from Egypt Real Estate Data 2026 (Kaggle)."]
            if is_rental:
                desc_parts.append("RENTAL LISTING — cash_price is annual rent, NOT a sale price.")
            if payment_method == "installments":
                desc_parts.append(
                    "Installment listing — cash_price may be the installment total, "
                    "not a cash-equivalent (source data is ambiguous)."
                )
            if lat and lon:
                desc_parts.append(f"GPS: {lat}, {lon}.")
            if raw_desc:
                desc_parts.append(raw_desc[:300])

            provenance_dict = {
                "source_dataset": "Egypt Real Estate Data 2026 (Kaggle)",
                "listing_type": "rental" if is_rental else "sale",
                "payment_method_raw": payment_method,
                "gps": [lat, lon] if (lat and lon) else None,
                "agency": agency,
            }

            ptype_str = ptype.replace("_", " ").title()
            title = f"{ptype_str} in {compound_name or location}"
            if is_rental:
                title = f"[RENT] {title}"

            cur = conn.execute("""
                INSERT INTO properties
                (title, compound, location, property_type, area_sqm, bedrooms, bathrooms,
                 finishing, developer, cash_price, description, data_source,
                 source_url, provenance_details)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                title, compound_name, location, ptype, area, bedrooms, bathrooms,
                finishing, agency, price,
                " ".join(desc_parts),
                "real_kaggle_2026",
                source_url,
                json.dumps(provenance_dict, ensure_ascii=False),
            ))
            prop_id = cur.lastrowid
            inserted += 1

            # Installment plan — sale listings only
            # Checks if listing has structured installment flag OR if description has installment terms
            if not is_rental and (payment_method == "installments" or "installment" in raw_desc.lower() or "أقساط" in raw_desc or "تقسيط" in raw_desc):
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

    print(f"\n✓ Inserted {inserted:,} real properties (data_source='real_kaggle_2026').")
    print(f"  Created {plans_created:,} installment plans (with extracted terms & provenance).")
    print(f"\nSkipped rows:")
    for reason, count in skipped.items():
        if count:
            print(f"  {reason:25s}: {count:,}")
    total_skipped = sum(skipped.values())
    total_rows = inserted + total_skipped
    print(f"\n  Total input rows ~{total_rows:,} | kept {inserted/max(total_rows,1)*100:.1f}%")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest Egypt Real Estate Data 2026 (Kaggle) into copilot.db"
    )
    parser.add_argument("--csv", required=True, help="Path to the downloaded CSV file")
    parser.add_argument("--reset-db", action="store_true",
                        help="Wipe and reinitialise the database before ingesting")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only ingest the first N rows (useful for testing)")
    parser.add_argument("--sale-only", action="store_true",
                        help="Skip rental listings (include only for-sale properties)")
    args = parser.parse_args()
    ingest(args.csv, reset_db=args.reset_db, limit=args.limit, sale_only=args.sale_only)
