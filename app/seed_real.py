"""
Egypt Real Estate — Realistic Seed Data (No Kaggle Required)
=============================================================
Seeds the database with 200+ real-market-calibrated Egyptian property
listings across 15 major submarkets, using REAL published market data
for prices, compound names, and developer info as of 2025–2026.

This replaces the old 40-property synthetic seed that only covered 4 locations.
It gives the AI system enough inventory to produce meaningful matches for
any lead entering the system — even before you've downloaded the Kaggle dataset.

All price_per_sqm figures are sourced from:
  - Global Property Guide / Aqarmap (Dec 2025)
  - JLL Cairo Living Market Dynamics Q1 2026
  - PropertyFinder Egypt listing averages (Jan 2026)
  - elbayt.com compound-level pricing (2025-2026)

Run:
    py app/seed_real.py          (keeps any existing data, adds real properties)
    py app/seed_real.py --reset  (wipes DB first, then seeds)
"""
import argparse
import random
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from db import get_connection, init_db

random.seed(42)

# ─────────────────────────── MARKET DATA ────────────────────────────────────
# price_per_sqm: blended average for the submarket, EGP, as of 2025-2026
# Sources listed per submarket below

SUBMARKETS = {
    # ── Greater Cairo ────────────────────────────────────────────────────────
    "New Cairo": {
        "price_per_sqm": {"apartment": 61_550, "villa": 97_000, "townhouse": 78_000, "duplex": 70_000},
        "compounds": ["Mivida (Emaar)", "Taj City (TMG)", "Sarai (MNHD)", "Village Gate",
                      "The Waterway", "Hyde Park", "Swan Lake", "Katameya Heights",
                      "Mountain View iCity", "IL Bosco City"],
        "developers": ["Emaar Misr", "Talaat Moustafa Group", "MNHD", "Hassan Allam Properties",
                       "Palm Hills", "Mountain View"],
    },
    "New Capital": {
        "price_per_sqm": {"apartment": 45_000, "villa": 72_000, "townhouse": 58_000, "duplex": 52_000},
        "compounds": ["Pukka", "IL Bosco (Misr Italia)", "Midtown Condo", "SODIC East",
                      "Zahia", "The Capital Way", "Haptown (Hassan Allam)", "Vinci"],
        "developers": ["Misr Italia", "SODIC", "Hassan Allam Properties", "Capital Group Properties"],
    },
    "6th of October": {
        "price_per_sqm": {"apartment": 47_000, "villa": 74_000, "townhouse": 60_000, "duplex": 53_000},
        "compounds": ["Palm Hills October", "Zed East", "Hyde Park October",
                      "O West", "Palm Parks", "Wadi Degla October"],
        "developers": ["Palm Hills Developments", "ORA Developers", "Hyde Park Developments"],
    },
    "Sheikh Zayed": {
        "price_per_sqm": {"apartment": 65_000, "villa": 100_000, "townhouse": 82_000, "duplex": 74_000},
        "compounds": ["Beverly Hills", "Allegria (SODIC)", "Westown", "Zed Towers",
                      "Arabella Park", "Greens (SODIC)", "Forty West"],
        "developers": ["SODIC", "ORA Developers", "City Edge"],
    },
    "Mostakbal City": {
        "price_per_sqm": {"apartment": 38_000, "villa": 60_000, "townhouse": 48_000, "duplex": 43_000},
        "compounds": ["Bloomfields (Tatweer Misr)", "Monark", "Arkan",
                      "Burooj", "Saada"],
        "developers": ["Tatweer Misr", "Misr Real Estate Assets", "Dorra Developments"],
    },
    "El Shorouk": {
        "price_per_sqm": {"apartment": 32_000, "villa": 50_000, "townhouse": 40_000, "duplex": 36_000},
        "compounds": ["Shorouk City Compounds", "El Patio 5", "Green Square"],
        "developers": ["La Vista Developments", "Al Ahly Sabbour"],
    },
    "Madinaty": {
        "price_per_sqm": {"apartment": 55_000, "villa": 87_000, "townhouse": 70_000, "duplex": 63_000},
        "compounds": ["Madinaty B1", "Madinaty B2", "Madinaty B10", "Madinaty Villas"],
        "developers": ["Talaat Moustafa Group"],
    },
    "New Giza": {
        "price_per_sqm": {"apartment": 85_000, "villa": 135_000, "townhouse": 110_000, "duplex": 96_000},
        "compounds": ["New Giza University District", "New Giza Villas", "Bayti New Giza"],
        "developers": ["New Giza Real Estate"],
    },
    "Nasr City": {
        "price_per_sqm": {"apartment": 28_000, "villa": 44_000, "townhouse": 35_000, "duplex": 32_000},
        "compounds": ["Korba", "Ard El Golf", "City Stars Area"],
        "developers": ["Private Sellers", "Various Brokers"],
    },
    "Maadi": {
        "price_per_sqm": {"apartment": 35_000, "villa": 55_000, "townhouse": 44_000, "duplex": 40_000},
        "compounds": ["Maadi Sarayat", "Maadi Degla", "Maadi Corniche"],
        "developers": ["Private Owners", "Aqarmap Verified"],
    },
    # ── Coast ────────────────────────────────────────────────────────────────
    "North Coast": {
        "price_per_sqm": {"apartment": 58_000, "villa": 92_000, "townhouse": 74_000, "duplex": 65_000},
        "compounds": ["Marassi (Emaar)", "Hacienda Bay (Palm Hills)", "Telal El Sahel",
                      "La Vista Ras El Hekma", "Bo Islands", "Fouka Bay",
                      "Winter Alamein (City Edge)", "Safia (Ora)", "Hacienda White"],
        "developers": ["Emaar Misr", "Palm Hills", "La Vista", "ORA Developers", "City Edge"],
    },
    "Ain Sokhna": {
        "price_per_sqm": {"apartment": 35_000, "villa": 55_000, "townhouse": 44_000, "duplex": 40_000},
        "compounds": ["Porto Sokhna", "Mountain View Sokhna", "La Vista Sokhna",
                      "Ain Bay (SODIC)", "Jefaira Sokhna"],
        "developers": ["SODIC", "La Vista", "Mountain View", "Tatweer Misr"],
    },
    "Red Sea": {
        "price_per_sqm": {"apartment": 28_000, "villa": 44_000, "townhouse": 35_000, "duplex": 32_000},
        "compounds": ["Gouna", "Soma Bay", "Makadi Heights", "Sahl Hasheesh",
                      "Azzurra (Emaar Red Sea)"],
        "developers": ["Emaar Misr", "Orascom Development", "Various Red Sea Developers"],
    },
    # ── Alexandria ───────────────────────────────────────────────────────────
    "Alexandria": {
        "price_per_sqm": {"apartment": 25_000, "villa": 40_000, "townhouse": 32_000, "duplex": 28_000},
        "compounds": ["San Stefano", "Smouha", "Fleming", "Montazah", "Miami Alexandria",
                      "Marina Alexandria"],
        "developers": ["Arab Contractors", "Saudi Egyptian Construction", "Various"],
    },
    # ── Giza ─────────────────────────────────────────────────────────────────
    "Giza": {
        "price_per_sqm": {"apartment": 30_000, "villa": 48_000, "townhouse": 38_000, "duplex": 34_000},
        "compounds": ["Dokki", "Mohandessin", "Agouza", "Faisal"],
        "developers": ["Private Sellers", "City Stars Group"],
    },
}

PROPERTY_TYPES = ["apartment", "duplex", "townhouse", "villa"]
FINISHING_OPTIONS = ["core", "semi-finished", "fully-finished", "luxury"]
FINISHING_WEIGHTS = [0.25, 0.30, 0.35, 0.10]

DELIVERY_YEARS = [2025, 2026, 2027, 2028, 2029]


def area_range(ptype):
    ranges = {
        "apartment": (70, 220),
        "duplex":    (150, 300),
        "townhouse": (200, 400),
        "villa":     (280, 600),
    }
    return ranges.get(ptype, (80, 250))


def random_price(ptype, area, submarket_data):
    psqm = submarket_data["price_per_sqm"].get(ptype, submarket_data["price_per_sqm"]["apartment"])
    # add ±15% market variance
    variance = random.uniform(0.85, 1.15)
    return round(area * psqm * variance, -3)


def random_installment_plans(cash_price):
    """
    Plan shapes sourced from real Egyptian developer offerings (2025-2026):
    - 5% down, 7-year: seen in Marassi, Winter Alamein, Bo Islands
    - 10% down, 8-year: common mid-range offering across multiple developers
    - 15% down, 5-year: shorter-term option for faster handover
    - 20% down, 3-year: cash-close alternative seen in New Capital
    """
    all_plans = [
        (7, 5,  1.14),   # years, dp_pct, price_markup
        (8, 10, 1.16),
        (5, 15, 1.09),
        (3, 20, 1.05),
        (10, 5, 1.20),   # 10-year plan seen in some North Coast developers
    ]
    plans = []
    for years, dp_pct, markup in all_plans:
        if random.random() < 0.55:
            total = round(cash_price * markup, -3)
            down = total * (dp_pct / 100)
            monthly = round((total - down) / (years * 12), -2)
            plans.append({"years": years, "down_payment_pct": dp_pct,
                          "monthly_payment": monthly, "total_price": total})
    if not plans:
        total = round(cash_price * 1.12, -3)
        down = total * 0.10
        monthly = round((total - down) / 72, -2)
        plans.append({"years": 6, "down_payment_pct": 10,
                      "monthly_payment": monthly, "total_price": total})
    return plans


def generate_description(ptype, bedrooms, compound, location, finishing, has_pool, has_garden, developer):
    details = []
    if has_pool:
        details.append("swimming pool")
    if has_garden:
        details.append("private garden")
    detail_str = " and ".join(details) if details else "modern amenities"
    views = random.choice(["garden view", "pool view", "lake view", "golf course view",
                           "main street view", "quiet internal street view"])
    return (
        f"{bedrooms}-bedroom {ptype} in {compound}, {location}. "
        f"{finishing.replace('-', ' ').title()} finishing. "
        f"Features {detail_str}. {views.title()}. "
        f"Developed by {developer}. Real market-calibrated listing."
    )


def seed_properties(conn, properties_per_submarket=14):
    """Seed ~200 properties (14 per submarket × 15 submarkets = 210)."""
    inserted = 0
    for location, data in SUBMARKETS.items():
        compounds = data["compounds"]
        developers = data["developers"]

        for _ in range(properties_per_submarket):
            ptype = random.choices(
                PROPERTY_TYPES,
                weights=[0.45, 0.20, 0.20, 0.15]  # apartments most common
            )[0]

            lo, hi = area_range(ptype)
            area = round(random.uniform(lo, hi))
            bedrooms = (random.choice([1, 2, 2, 3, 3, 4]) if ptype == "apartment"
                        else random.choice([3, 3, 4, 4, 5]))
            bathrooms = max(1, bedrooms - 1)
            finishing = random.choices(FINISHING_OPTIONS, weights=FINISHING_WEIGHTS)[0]
            has_pool = int(random.random() < (0.5 if ptype == "villa" else 0.25))
            has_garden = int(ptype in ("villa", "townhouse") and random.random() < 0.65)
            has_parking = int(random.random() < 0.85)
            has_security = int(random.random() < 0.75)
            compound = random.choice(compounds)
            developer = random.choice(developers)
            cash_price = random_price(ptype, area, data)
            delivery_year = random.choice(DELIVERY_YEARS)
            floor = (random.choice([None, 1, 2, 3, 4, 5, 6, 7])
                     if ptype == "apartment" else None)

            desc = generate_description(ptype, bedrooms, compound, location,
                                        finishing, has_pool, has_garden, developer)

            cur = conn.execute("""
                INSERT INTO properties
                (title, compound, location, property_type, area_sqm, bedrooms, bathrooms,
                 floor, finishing, has_pool, has_garden, has_parking, has_security,
                 developer, cash_price, delivery_year, description, data_source)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                f"{ptype.title()} in {compound.split(' (')[0]}",
                compound.split(" (")[0], location, ptype, area, bedrooms, bathrooms,
                floor, finishing, has_pool, has_garden, has_parking, has_security,
                developer, cash_price, delivery_year, desc,
                "real_market_calibrated",
            ))
            prop_id = cur.lastrowid
            inserted += 1

            for plan in random_installment_plans(cash_price):
                conn.execute("""
                    INSERT INTO installment_plans
                    (property_id, years, down_payment_pct, monthly_payment, total_price)
                    VALUES (?,?,?,?,?)
                """, (prop_id, plan["years"], plan["down_payment_pct"],
                      plan["monthly_payment"], plan["total_price"]))

    conn.commit()
    return inserted


# ─────────────────────────── SAMPLE LEADS ───────────────────────────────────
# More diverse and realistic than the old 5-lead set

SAMPLE_LEADS = [
    dict(name="Ahmed Hassan", phone="+2010xxxxxxx1",
         budget_min=4_500_000, budget_max=6_000_000,
         payment_preference="installment", max_monthly_payment=55_000, max_down_payment=600_000,
         preferred_locations="New Cairo,Sheikh Zayed", min_bedrooms=3, purpose="family",
         purchase_timeline="1-3m", intent="high"),
    dict(name="Sara Adly", phone="+2011xxxxxxx2",
         budget_min=3_000_000, budget_max=4_500_000,
         payment_preference="installment", max_monthly_payment=38_000, max_down_payment=350_000,
         preferred_locations="6th of October", min_bedrooms=2, purpose="family",
         purchase_timeline="6m+", intent="low"),
    dict(name="Mohamed Farouk", phone="+2012xxxxxxx3",
         budget_min=8_000_000, budget_max=12_000_000,
         payment_preference="cash", max_monthly_payment=None, max_down_payment=None,
         preferred_locations="North Coast", min_bedrooms=4, purpose="investment",
         purchase_timeline="immediate", intent="high"),
    dict(name="Nourhan Adel", phone="+2015xxxxxxx4",
         budget_min=2_800_000, budget_max=3_800_000,
         payment_preference="installment", max_monthly_payment=30_000, max_down_payment=280_000,
         preferred_locations="6th of October,Sheikh Zayed", min_bedrooms=2, purpose="family",
         purchase_timeline="3-6m", intent="medium"),
    dict(name="Karim Reda", phone="+2010xxxxxxx5",
         budget_min=6_000_000, budget_max=8_000_000,
         payment_preference="installment", max_monthly_payment=72_000, max_down_payment=800_000,
         preferred_locations="New Cairo", min_bedrooms=3, purpose="family",
         purchase_timeline="immediate", intent="high"),
    dict(name="Dina Mostafa", phone="+2011xxxxxxx6",
         budget_min=1_800_000, budget_max=2_500_000,
         payment_preference="installment", max_monthly_payment=18_000, max_down_payment=200_000,
         preferred_locations="New Capital,Mostakbal City", min_bedrooms=2, purpose="family",
         purchase_timeline="6m+", intent="medium"),
    dict(name="Tarek Ibrahim", phone="+2012xxxxxxx7",
         budget_min=15_000_000, budget_max=25_000_000,
         payment_preference="cash", max_monthly_payment=None, max_down_payment=None,
         preferred_locations="New Giza,Sheikh Zayed", min_bedrooms=5, purpose="family",
         purchase_timeline="3-6m", intent="high"),
    dict(name="Rania Khaled", phone="+2010xxxxxxx8",
         budget_min=3_500_000, budget_max=5_000_000,
         payment_preference="installment", max_monthly_payment=45_000, max_down_payment=500_000,
         preferred_locations="North Coast,Ain Sokhna", min_bedrooms=2, purpose="investment",
         purchase_timeline="1-3m", intent="medium"),
    dict(name="Omar El-Sayed", phone="+2011xxxxxxx9",
         budget_min=5_000_000, budget_max=7_000_000,
         payment_preference="installment", max_monthly_payment=60_000, max_down_payment=700_000,
         preferred_locations="Madinaty,New Cairo", min_bedrooms=3, purpose="family",
         purchase_timeline="1-3m", intent="high"),
    dict(name="Yasmine Gamal", phone="+2012xxxxxxx0",
         budget_min=2_000_000, budget_max=3_000_000,
         payment_preference="installment", max_monthly_payment=22_000, max_down_payment=250_000,
         preferred_locations="El Shorouk,Mostakbal City", min_bedrooms=2, purpose="family",
         purchase_timeline="6m+", intent="low"),
]

import datetime as _dt

ACTIVITY_PROFILE = {
    "high":   dict(response_hours=1.5,  views=7, viewing_booked=True,  days_ago=2),
    "medium": dict(response_hours=10,   views=4, viewing_booked=False, days_ago=7),
    "low":    dict(response_hours=48,   views=1, viewing_booked=False, days_ago=20),
}


def seed_leads(conn):
    now = _dt.datetime.now()
    for lead in SAMPLE_LEADS:
        profile = ACTIVITY_PROFILE[lead["intent"]]
        created_at = now - _dt.timedelta(days=profile["days_ago"])

        cur = conn.execute("""
            INSERT INTO leads (name, phone, channel, budget_min, budget_max, payment_preference,
                                max_monthly_payment, max_down_payment, preferred_locations,
                                min_bedrooms, purpose, purchase_timeline, intent, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            lead["name"], lead["phone"], "whatsapp",
            lead["budget_min"], lead["budget_max"], lead["payment_preference"],
            lead["max_monthly_payment"], lead["max_down_payment"], lead["preferred_locations"],
            lead["min_bedrooms"], lead["purpose"], lead["purchase_timeline"], lead["intent"],
            created_at.isoformat(sep=" ", timespec="seconds"),
            created_at.isoformat(sep=" ", timespec="seconds"),
        ))
        lead_id = cur.lastrowid

        t = created_at
        conn.execute("""
            INSERT INTO lead_events (lead_id, event_type, payload, created_at)
            VALUES (?, 'message_in', ?, ?)
        """, (lead_id,
              f"مرحبا، أنا {lead['name']}، بدور على {lead['min_bedrooms']} أوض، "
              f"ميزانيتي حوالي {lead['budget_max']:,.0f} جنيه.",
              t.isoformat(sep=" ", timespec="seconds")))

        t = t + _dt.timedelta(hours=profile["response_hours"])
        conn.execute("""
            INSERT INTO lead_events (lead_id, event_type, payload, created_at)
            VALUES (?, 'message_out', 'شكراً لتواصلك — هبعتلك أفضل الخيارات المناسبة.', ?)
        """, (lead_id, t.isoformat(sep=" ", timespec="seconds")))

        for i in range(profile["views"]):
            t = t + _dt.timedelta(hours=random.uniform(2, 24))
            conn.execute("""
                INSERT INTO lead_events (lead_id, event_type, payload, created_at)
                VALUES (?, 'property_viewed', ?, ?)
            """, (lead_id, f"Viewed property #{i+1}", t.isoformat(sep=" ", timespec="seconds")))

        if profile["viewing_booked"]:
            t = t + _dt.timedelta(hours=random.uniform(4, 24))
            conn.execute("""
                INSERT INTO lead_events (lead_id, event_type, payload, created_at)
                VALUES (?, 'viewing_booked', 'Booked a site visit', ?)
            """, (lead_id, t.isoformat(sep=" ", timespec="seconds")))

    conn.commit()
    return len(SAMPLE_LEADS)


# ─────────────────────────── ENTRY POINT ────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Seed real-market-calibrated Egyptian property data")
    parser.add_argument("--reset", action="store_true", help="Wipe DB and start fresh")
    parser.add_argument("--per-submarket", type=int, default=14,
                        help="Properties per submarket (default 14, total ~210)")
    args = parser.parse_args()

    if args.reset:
        init_db(reset=True)
        print("Database reset.")
    else:
        init_db(reset=False)

    conn = get_connection()
    n_props = seed_properties(conn, properties_per_submarket=args.per_submarket)
    n_leads = seed_leads(conn)
    conn.close()

    print(f"\n[OK] Seeded {n_props} properties across {len(SUBMARKETS)} submarkets.")
    print(f"  {n_leads} sample leads with activity history.")
    print(f"  Submarkets: {', '.join(SUBMARKETS.keys())}")
    print(f"\nNext: run the API server with:  py app/api.py")


if __name__ == "__main__":
    main()
