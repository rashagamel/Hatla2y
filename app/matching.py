"""
Rule-based, installment-aware matching engine.

Key idea: a lead's "budget" is not comparable to a property's cash price unless
we know whether they're paying cash or on a plan. If they're on an installment
plan, we compare against the BEST-FITTING plan's monthly payment and down
payment, AND we still check that the unit's price is inside the stated budget
range (a unit far outside the budget is not a realistic match even if some plan
fits the monthly cap).

Score breakdown:
    affordability (plan / cash fit)   25%
    price fit (inside budget range)   15%
    location                          25%
    bedrooms                          10%
    payment plan fit                  15%
    amenities/finishing               10%

Hard constraints (location, min bedrooms, price far over budget, plan that does
not fit stated limits) cap the final score at 45% and are returned in
`hard_fail`, so callers can tell "weak match" from "does not meet requirements".
"""
import json
from collections import defaultdict
from db import get_connection

PRICE_TOLERANCE = 0.15      # up to 15% over budget max is a soft warning, beyond is a hard fail
FAR_BELOW_RATIO = 0.75      # below 75% of budget min is "far below" (soft)
HARD_FAIL_CAP = 0.45        # final score cap when any hard constraint fails
VIABLE_MIN_SCORE = 60.0     # a match counts as "viable" at or above this score


def get_all_property_plans(conn):
    """Fetches every installment plan in one query and groups by property_id."""
    plans_by_property = defaultdict(list)
    for row in conn.execute("SELECT * FROM installment_plans"):
        plans_by_property[row["property_id"]].append(dict(row))
    return plans_by_property


def has_plan_limits(lead):
    return bool(lead.get("max_monthly_payment") or lead.get("max_down_payment"))


def best_affordable_plan(plans, max_monthly, max_down):
    """Return the plan that fits the lead's constraints with the shortest term,
    or the closest-fitting plan if none fit exactly."""
    if not plans:
        return None, False
    fitting = [
        p for p in plans
        if (max_monthly is None or p["monthly_payment"] <= max_monthly)
        and (max_down is None or (p["total_price"] * p["down_payment_pct"] / 100) <= max_down)
    ]
    if fitting:
        return min(fitting, key=lambda p: p["years"]), True
    return min(plans, key=lambda p: p["monthly_payment"]), False


def score_price_fit(prop, lead):
    """Is the unit's price inside the lead's stated budget range?
    Returns (score_0_to_1, reason, status) where status is one of:
    'unknown', 'ok', 'soft_over', 'hard_over', 'below', 'far_below'."""
    bmin, bmax = lead.get("budget_min"), lead.get("budget_max")
    price = prop["cash_price"]
    if not bmin and not bmax:
        return 0.5, "No budget range stated", "unknown"

    if bmax and price > bmax * (1 + PRICE_TOLERANCE):
        over = (price - bmax) / bmax * 100
        return 0.0, f"Price {price:,.0f} EGP is {over:.0f}% above your budget max", "hard_over"
    if bmax and price > bmax:
        over = (price - bmax) / bmax * 100
        return 0.5, f"Price {price:,.0f} EGP is slightly above budget ({over:.0f}% over)", "soft_over"
    if bmin and price < bmin * FAR_BELOW_RATIO:
        return 0.3, f"Price {price:,.0f} EGP is far below your stated budget range", "far_below"
    if bmin and price < bmin:
        return 0.7, f"Price {price:,.0f} EGP is just below your stated range", "below"

    # inside the range: prefer prices closer to the middle of it
    if bmin and bmax and bmax > bmin:
        mid, half = (bmin + bmax) / 2, (bmax - bmin) / 2
        dev = min(abs(price - mid) / half, 1.0)
        return 1.0 - 0.25 * dev, f"Price {price:,.0f} EGP is within your budget range", "ok"
    return 1.0, f"Price {price:,.0f} EGP is within your budget", "ok"


def score_affordability(prop, plans, lead):
    """Returns (score_0_to_1, reason_string, fits) where fits is True, False,
    or None when there is nothing stated to verify against."""
    pref = lead.get("payment_preference")

    if pref == "cash":
        budget_max = lead.get("budget_max") or 0
        budget_min = lead.get("budget_min") or 0
        price = prop["cash_price"]
        if budget_min <= price <= budget_max:
            return 1.0, f"Cash price {price:,.0f} EGP is within your budget", True
        if price < budget_min:
            return 0.85, f"Cash price {price:,.0f} EGP is below your stated budget (good value)", True
        if not budget_max:
            return 0.5, "No cash budget stated", None
        overshoot = (price - budget_max) / budget_max
        if overshoot <= 0.1:
            return 0.6, f"Cash price {price:,.0f} EGP is slightly above budget ({overshoot*100:.0f}% over)", False
        return max(0.0, 0.4 - overshoot), f"Cash price {price:,.0f} EGP is well above budget", False

    if pref == "installment" or has_plan_limits(lead):
        if not plans:
            return 0.3, "No installment plan available for this property", False
        max_monthly = lead.get("max_monthly_payment")
        max_down = lead.get("max_down_payment")
        if not has_plan_limits(lead):
            return 0.6, "No monthly or down-payment limit stated yet, so plan fit can't be verified", None

        plan, fits = best_affordable_plan(plans, max_monthly, max_down)
        down_payment = plan["total_price"] * plan["down_payment_pct"] / 100
        if fits:
            # reward comfortable headroom under the monthly cap so ranking differentiates
            headroom = 0.0
            if max_monthly:
                headroom = max(0.0, min(1.0, 1 - plan["monthly_payment"] / max_monthly))
            score = 0.8 + 0.2 * min(headroom / 0.3, 1.0)
            return score, (f"{plan['years']}-year plan fits: {plan['monthly_payment']:,.0f} EGP/month, "
                           f"{down_payment:,.0f} EGP down payment"), True
        monthly_gap = plan["monthly_payment"] - (max_monthly or plan["monthly_payment"])
        if max_monthly and monthly_gap > 0:
            gap_pct = monthly_gap / max_monthly
            return max(0.0, 0.55 - gap_pct), (
                f"Closest plan is {plan['years']} years at {plan['monthly_payment']:,.0f} EGP/month "
                f"(~{monthly_gap:,.0f} EGP/month above your stated max)"), False
        return 0.4, (f"Closest plan: {plan['years']} years at {plan['monthly_payment']:,.0f} EGP/month, "
                     f"down payment above your stated max"), False

    return 0.5, "Payment preference not stated yet", None


def _norm(s):
    return (s or "").strip().lower()


def score_location(prop, lead):
    preferred = [l.strip() for l in (lead.get("preferred_locations") or "").split(",") if l.strip()]
    if not preferred:
        return 0.5, "No location preference stated"
    if _norm(prop["location"]) in [_norm(p) for p in preferred]:
        return 1.0, f"In your preferred location ({prop['location']})"
    return 0.15, f"{prop['location']} is not in your preferred locations"


def score_bedrooms(prop, lead):
    min_beds = lead.get("min_bedrooms")
    beds = prop.get("bedrooms") or 0
    if not min_beds:
        return 0.5, "No bedroom preference stated"
    if beds >= min_beds:
        extra = beds - min_beds
        return (1.0 if extra <= 1 else 0.8), f"{beds} bedrooms meets your minimum of {min_beds}"
    return 0.1, f"Only {beds} bedrooms, below your minimum of {min_beds}"


def score_amenities(prop):
    score = 0.4
    reasons = []
    if prop.get("finishing") in ("fully-finished", "luxury"):
        score += 0.3
        reasons.append(f"{prop['finishing']} finishing")
    if prop.get("has_parking"):
        score += 0.15
        reasons.append("parking included")
    if prop.get("has_security"):
        score += 0.15
        reasons.append("24/7 security")
    return min(score, 1.0), (", ".join(reasons) if reasons else "Standard amenities")


WEIGHTS = {
    "affordability": 0.25,
    "price_fit": 0.15,
    "location": 0.25,
    "bedrooms": 0.10,
    "payment_fit": 0.15,
    "amenities": 0.10,
}


def evaluate_property(prop, plans, lead):
    """Scores one property against one lead. Pure function (no DB), so it can be
    unit tested. Returns the result dict used by the API."""
    aff_score, aff_reason, fits = score_affordability(prop, plans, lead)
    price_score, price_reason, price_status = score_price_fit(prop, lead)
    loc_score, loc_reason = score_location(prop, lead)
    bed_score, bed_reason = score_bedrooms(prop, lead)
    amen_score, amen_reason = score_amenities(prop)

    payment_fit_score = 1.0 if fits is True else (0.3 if fits is False else 0.6)

    total = (
        aff_score * WEIGHTS["affordability"]
        + price_score * WEIGHTS["price_fit"]
        + loc_score * WEIGHTS["location"]
        + bed_score * WEIGHTS["bedrooms"]
        + payment_fit_score * WEIGHTS["payment_fit"]
        + amen_score * WEIGHTS["amenities"]
    )

    # hard constraints: cap the score and report why
    hard_fail = []
    if loc_score <= 0.15:
        hard_fail.append("location")
    if bed_score <= 0.1:
        hard_fail.append("bedrooms")
    if price_status == "hard_over":
        hard_fail.append("price")
    installment_buyer = lead.get("payment_preference") == "installment" or has_plan_limits(lead)
    if installment_buyer and fits is False:
        hard_fail.append("payment_plan")
    if hard_fail:
        total = min(total, HARD_FAIL_CAP)

    reasons, warnings = [], []
    for score, reason in [(aff_score, aff_reason), (price_score, price_reason),
                          (loc_score, loc_reason), (bed_score, bed_reason),
                          (amen_score, amen_reason)]:
        (reasons if score >= 0.5 else warnings).append(reason)
    if fits is False and "Outside your stated payment comfort zone" not in warnings:
        warnings.append("Outside your stated payment comfort zone")

    return {
        "property_id": prop["id"],
        "title": prop["title"],
        "compound": prop.get("compound"),
        "location": prop["location"],
        "property_type": prop.get("property_type"),
        "area_sqm": prop.get("area_sqm"),
        "finishing": prop.get("finishing"),
        "cash_price": prop["cash_price"],
        "bedrooms": prop.get("bedrooms"),
        "score": round(total * 100, 1),
        "reasons": reasons,
        "warnings": warnings,
        "hard_fail": hard_fail,
        "plan_fits": fits,
        "price_status": price_status,
    }


def match_lead_to_properties(lead_id, top_n=5, only_available=True, log_matches=True):
    """top_n=None returns every scored property. log_matches=False scores without
    writing to the matches table (used by analytics such as lead intelligence)."""
    conn = get_connection()
    lead_row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead_row:
        conn.close()
        raise ValueError(f"Lead {lead_id} not found")
    lead = dict(lead_row)

    query = "SELECT * FROM properties"
    if only_available:
        query += " WHERE status = 'available'"
    properties = [dict(r) for r in conn.execute(query).fetchall()]
    plans_by_property = get_all_property_plans(conn)

    results = [evaluate_property(p, plans_by_property.get(p["id"], []), lead) for p in properties]
    results.sort(key=lambda r: r["score"], reverse=True)
    top_results = results if top_n is None else results[:top_n]

    if log_matches:
        # keep only the latest result set per lead; appending on every request made the
        # table grow and any COUNT(*) over it meaningless
        conn.execute("DELETE FROM matches WHERE lead_id = ?", (lead_id,))
        for r in top_results[:20]:
            conn.execute("""
                INSERT INTO matches (lead_id, property_id, score, reasons)
                VALUES (?,?,?,?)
            """, (lead_id, r["property_id"], r["score"] / 100, json.dumps(r["reasons"])))
        conn.commit()
    conn.close()

    return top_results


if __name__ == "__main__":
    import sys
    lead_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    matches = match_lead_to_properties(lead_id)
    print(f"\nTop matches for lead {lead_id}:\n")
    for m in matches:
        print(f"  {m['score']:5.1f}%  {m['title']} ({m['compound']}, {m['location']}) - {m['cash_price']:,.0f} EGP")
        for r in m["reasons"]:
            print(f"          + {r}")
        for w in m["warnings"]:
            print(f"          ! {w}")
        print()