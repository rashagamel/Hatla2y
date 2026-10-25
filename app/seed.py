"""
Seed the database with realistic-looking sample data:
- ~40 properties across New Cairo, 6th of October, Sheikh Zayed, North Coast
- each with a cash price + 1-3 installment plans
- ~15 sample leads with varying profiles
"""
import random
from db import get_connection, init_db

random.seed(42)

COMPOUNDS = {
    "New Cairo": ["Mivida", "Taj City", "Sarai", "Village Gate", "The Waterway"],
    "6th of October": ["Palm Hills October", "Zed East", "Hyde Park October", "O West"],
    "Sheikh Zayed": ["Beverly Hills", "Allegria", "Westown", "Zed Towers"],
    "North Coast": ["Marassi", "Hacienda Bay", "Telal", "La Vista"],
}

PROPERTY_TYPES = ["apartment", "duplex", "townhouse", "villa"]
FINISHING = ["core", "semi-finished", "fully-finished", "luxury"]
DEVELOPERS = ["Emaar Misr", "Palm Hills Developments", "SODIC", "Ora Developers", "Talaat Moustafa Group"]


def price_for(property_type, area, location):
    # Calibrated to real Egyptian market data (not invented). Sources:
    #   New Cairo:      61,550 EGP/sqm apartments, 97,000 EGP/sqm villas (Dec 2025, Aqarmap
    #                    via Global Property Guide, globalpropertyguide.com/middle-east/egypt/price-history)
    #   6th of October: 47,000 EGP/sqm apartments (same source, Dec 2025)
    #   Sheikh Zayed:   grouped with New Cairo among the priciest Cairo submarkets — JLL Cairo
    #                    Living Market Dynamics Q1 2026 / sandsofwealth.com puts prime areas at
    #                    EGP 60,000-160,000+/sqm; used a conservative point estimate slightly above
    #                    New Cairo's blended average
    #   North Coast:    wide observed range across compounds (elbayt.com listings, 2025-2026):
    #                    Bo Islands 22,500 EGP/sqm (entry), Winter Alamein 47,000, La Vista Ras
    #                    El Hekma 83,000, Safia 103,000/sqm — used a blended mid-point
    # Villa/apartment ratio (~1.55-1.6x) derived from the New Cairo apartment vs. villa figures
    # above and applied consistently across locations in the absence of per-location villa data.
    base_per_sqm = {
        "New Cairo": 61550,
        "6th of October": 47000,
        "Sheikh Zayed": 65000,
        "North Coast": 58000,
    }[location]
    multiplier = {"apartment": 1.0, "duplex": 1.15, "townhouse": 1.3, "villa": 1.58}[property_type]
    return round(area * base_per_sqm * multiplier, -3)


def make_installment_plans(cash_price):
    # Plan shapes (years, down-payment %, price markup vs cash) reflect terms actually seen
    # in current Egyptian developer listings, e.g. Bo Islands (North Coast): 5% down, 7-year/28
    # installments; Winter Alamein: 5% down, 7-year; Marassi (Emaar Misr): 5% down, up to 8 years
    # (elbayt.com, aqarland.net listings, 2025-2026). 10-15% down / shorter-term plans are also
    # commonly offered as faster-payoff alternatives.
    plans = []
    for years, dp_pct, markup in [(5, 10, 1.08), (7, 5, 1.14), (8, 15, 1.10)]:
        if random.random() < 0.7:  # not every property offers every plan
            total = round(cash_price * markup, -3)
            down_payment = total * (dp_pct / 100)
            monthly = round((total - down_payment) / (years * 12), -2)
            plans.append({
                "years": years,
                "down_payment_pct": dp_pct,
                "monthly_payment": monthly,
                "total_price": total,
            })
    if not plans:  # ensure at least one plan
        plans.append({
            "years": 6, "down_payment_pct": 10,
            "monthly_payment": round(cash_price * 1.1 * 0.9 / 72, -2),
            "total_price": round(cash_price * 1.1, -3),
        })
    return plans


def seed_properties(conn):
    property_ids = []
    for location, compounds in COMPOUNDS.items():
        for i in range(10):  # 10 properties per location = 40 total
            compound = random.choice(compounds)
            ptype = random.choice(PROPERTY_TYPES)
            bedrooms = random.choice([2, 3, 3, 4, 4, 5]) if ptype != "apartment" else random.choice([1, 2, 2, 3, 3])
            area = round(random.uniform(90, 320) if ptype in ("villa", "townhouse") else random.uniform(80, 220))
            cash_price = price_for(ptype, area, location)

            cur = conn.execute("""
                INSERT INTO properties
                (title, compound, location, property_type, area_sqm, bedrooms, bathrooms,
                 floor, finishing, has_pool, has_garden, has_parking, has_security,
                 developer, cash_price, delivery_year, description)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                f"{ptype.title()} in {compound}",
                compound, location, ptype, area, bedrooms, max(1, bedrooms - 1),
                random.choice([None, 1, 2, 3, 4, 5]) if ptype == "apartment" else None,
                random.choice(FINISHING),
                int(random.random() < 0.4), int(ptype in ("villa", "townhouse") and random.random() < 0.6),
                int(random.random() < 0.85), int(random.random() < 0.7),
                random.choice(DEVELOPERS), cash_price,
                random.choice([2026, 2027, 2028]),
                f"{bedrooms}-bedroom {ptype} in {compound}, {location}. "
                f"{random.choice(['Prime location', 'Quiet street', 'Near main gate', 'Corner unit', 'Garden view'])}.",
            ))
            prop_id = cur.lastrowid
            property_ids.append(prop_id)

            for plan in make_installment_plans(cash_price):
                conn.execute("""
                    INSERT INTO installment_plans (property_id, years, down_payment_pct, monthly_payment, total_price)
                    VALUES (?,?,?,?,?)
                """, (prop_id, plan["years"], plan["down_payment_pct"], plan["monthly_payment"], plan["total_price"]))
    conn.commit()
    return property_ids


SAMPLE_LEADS = [
    dict(name="Ahmed Hassan", phone="+2010xxxxxx1", budget_min=4_500_000, budget_max=5_500_000,
         payment_preference="installment", max_monthly_payment=55_000, max_down_payment=600_000,
         preferred_locations="New Cairo,Sheikh Zayed", min_bedrooms=3, purpose="family",
         purchase_timeline="1-3m", intent="high"),
    dict(name="Sara Adly", phone="+2011xxxxxx2", budget_min=3_000_000, budget_max=4_000_000,
         payment_preference="installment", max_monthly_payment=35_000, max_down_payment=300_000,
         preferred_locations="6th of October", min_bedrooms=2, purpose="family",
         purchase_timeline="6m+", intent="low"),
    dict(name="Mohamed Farouk", phone="+2012xxxxxx3", budget_min=8_000_000, budget_max=10_000_000,
         payment_preference="cash", max_monthly_payment=None, max_down_payment=None,
         preferred_locations="North Coast", min_bedrooms=4, purpose="investment",
         purchase_timeline="immediate", intent="high"),
    dict(name="Nourhan Adel", phone="+2015xxxxxx4", budget_min=2_800_000, budget_max=3_500_000,
         payment_preference="installment", max_monthly_payment=28_000, max_down_payment=250_000,
         preferred_locations="6th of October,Sheikh Zayed", min_bedrooms=2, purpose="family",
         purchase_timeline="3-6m", intent="medium"),
    dict(name="Karim Reda", phone="+2010xxxxxx5", budget_min=6_000_000, budget_max=7_500_000,
         payment_preference="installment", max_monthly_payment=70_000, max_down_payment=750_000,
         preferred_locations="New Cairo", min_bedrooms=3, purpose="family",
         purchase_timeline="immediate", intent="high"),
]


import datetime as _dt

# Activity profile per intent level — drives how realistic/differentiated the
# scoring model's output looks in the demo. High-intent leads respond fast,
# view more properties, and request viewings; low-intent leads go quiet.
ACTIVITY_PROFILE = {
    "high":   dict(response_hours=1.5,  views=6, viewing_booked=True,  days_ago=2),
    "medium": dict(response_hours=8,    views=3, viewing_booked=False, days_ago=6),
    "low":    dict(response_hours=48,   views=1, viewing_booked=False, days_ago=18),
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

        # event 0: inbound message at creation time
        t = created_at
        conn.execute("""
            INSERT INTO lead_events (lead_id, event_type, payload, created_at)
            VALUES (?, 'message_in', ?, ?)
        """, (lead_id, f"Hi, I'm {lead['name']}, looking for a {lead['min_bedrooms']}-bedroom place, "
                        f"budget around {lead['budget_max']:,.0f} EGP.",
              t.isoformat(sep=" ", timespec="seconds")))

        # event 1: agent's response, `response_hours` later
        t = t + _dt.timedelta(hours=profile["response_hours"])
        conn.execute("""
            INSERT INTO lead_events (lead_id, event_type, payload, created_at)
            VALUES (?, 'message_out', 'Thanks for reaching out — sending a few matching options.', ?)
        """, (lead_id, t.isoformat(sep=" ", timespec="seconds")))

        # property_viewed events, spaced out a bit
        for i in range(profile["views"]):
            t = t + _dt.timedelta(hours=random.uniform(2, 20))
            conn.execute("""
                INSERT INTO lead_events (lead_id, event_type, payload, created_at)
                VALUES (?, 'property_viewed', ?, ?)
            """, (lead_id, f"Viewed property #{i+1}", t.isoformat(sep=" ", timespec="seconds")))

        if profile["viewing_booked"]:
            t = t + _dt.timedelta(hours=random.uniform(4, 24))
            conn.execute("""
                INSERT INTO lead_events (lead_id, event_type, payload, created_at)
                VALUES (?, 'viewing_booked', 'Booked a site viewing', ?)
            """, (lead_id, t.isoformat(sep=" ", timespec="seconds")))
    conn.commit()


if __name__ == "__main__":
    init_db(reset=True)
    conn = get_connection()
    prop_ids = seed_properties(conn)
    seed_leads(conn)
    print(f"Seeded {len(prop_ids)} properties and {len(SAMPLE_LEADS)} leads.")
    conn.close()
