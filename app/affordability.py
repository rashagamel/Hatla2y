"""
Phase 4 — Advanced Installment / Affordability Intelligence.

The gap this fills: matching.py's score_affordability() already picks the
SINGLE best-fitting plan for a property to compute a match score — but it
never shows a customer's actual choice: multiple plans on the same
property, each labeled clearly (GREEN/YELLOW/RED), so an agent can say
"Plan B is the best fit" with a reason, instead of only ever seeing one
pre-selected plan. This module adds that comparison layer on top of the
EXISTING installment_plans data — no new pricing logic, no synthetic plan
generation, just clearer labeling and multi-plan comparison of what
matching.py and seed.py/ingest_kaggle.py already produced.

Also adds a lead-level affordability profile: given a lead's stated
constraints, what price range and plan shape can they actually work with
across the inventory — not property-specific, a general orientation.
"""
from db import get_connection

# Overshoot bands for GREEN/YELLOW/RED — same spirit as matching.py's existing
# score_affordability() overshoot handling (10% cash-price threshold), applied
# per-constraint instead of blended into one score. Documented as practical
# thresholds for this prototype, not statistically derived (same honesty
# standard as feedback.get_model_maturity's maturity stages).
YELLOW_OVERSHOOT_THRESHOLD = 0.15  # up to 15% over a stated constraint -> YELLOW, not RED


def assess_plan_affordability(plan: dict, lead: dict) -> dict:
    """Labels ONE installment plan against a lead's stated constraints.
    Returns GREEN (affordable), YELLOW (slightly above preference), or RED
    (financially unsuitable) with the specific reason — never hides the
    reasoning, per the spec's own requirement."""
    down_payment = plan["total_price"] * plan["down_payment_pct"] / 100
    max_monthly = lead.get("max_monthly_payment")
    max_down = lead.get("max_down_payment")

    monthly_overshoot = None
    if max_monthly:
        monthly_overshoot = (plan["monthly_payment"] - max_monthly) / max_monthly
    down_overshoot = None
    if max_down:
        down_overshoot = (down_payment - max_down) / max_down

    # Nothing known to compare against — can't claim affordability either way
    if monthly_overshoot is None and down_overshoot is None:
        return {
            "years": plan["years"], "monthly_payment": plan["monthly_payment"],
            "down_payment": round(down_payment, -2), "total_price": plan["total_price"],
            "label": "UNKNOWN",
            "reason": "No stated monthly-payment or down-payment constraint to compare against yet.",
        }

    worst_overshoot = max(o for o in (monthly_overshoot, down_overshoot) if o is not None)

    if worst_overshoot <= 0:
        reason_bits = []
        if monthly_overshoot is not None:
            reason_bits.append(f"{plan['monthly_payment']:,.0f} EGP/month fits your {max_monthly:,.0f} max")
        if down_overshoot is not None:
            reason_bits.append(f"{down_payment:,.0f} EGP down payment fits your {max_down:,.0f} max")
        return {
            "years": plan["years"], "monthly_payment": plan["monthly_payment"],
            "down_payment": round(down_payment, -2), "total_price": plan["total_price"],
            "label": "GREEN", "reason": "; ".join(reason_bits),
        }

    if worst_overshoot <= YELLOW_OVERSHOOT_THRESHOLD:
        over_bit = (f"monthly payment is {worst_overshoot*100:.0f}% above your stated max"
                    if worst_overshoot == monthly_overshoot else
                    f"down payment is {worst_overshoot*100:.0f}% above your stated max")
        return {
            "years": plan["years"], "monthly_payment": plan["monthly_payment"],
            "down_payment": round(down_payment, -2), "total_price": plan["total_price"],
            "label": "YELLOW", "reason": f"Close, but {over_bit}.",
        }

    over_bit = (f"monthly payment is {worst_overshoot*100:.0f}% above your stated max"
                if worst_overshoot == monthly_overshoot else
                f"down payment is {worst_overshoot*100:.0f}% above your stated max")
    return {
        "years": plan["years"], "monthly_payment": plan["monthly_payment"],
        "down_payment": round(down_payment, -2), "total_price": plan["total_price"],
        "label": "RED", "reason": f"Not financially suitable — {over_bit}.",
    }


def compare_property_plans(property_id: int, lead_id: int) -> dict:
    """Every plan on this property, labeled, plus which one is recommended
    and why. The recommendation prefers GREEN plans (shortest term among
    them, i.e. pays it off fastest while still affording it), then YELLOW,
    never silently recommends a RED plan."""
    conn = get_connection()
    prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
    if not prop:
        conn.close()
        raise ValueError(f"Property {property_id} not found")
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        conn.close()
        raise ValueError(f"Lead {lead_id} not found")
    plans = [dict(r) for r in conn.execute(
        "SELECT * FROM installment_plans WHERE property_id = ?", (property_id,)
    ).fetchall()]
    conn.close()

    prop, lead = dict(prop), dict(lead)
    assessed = [assess_plan_affordability(p, lead) for p in plans]

    label_rank = {"GREEN": 0, "YELLOW": 1, "UNKNOWN": 2, "RED": 3}
    best = None
    if assessed:
        best = min(assessed, key=lambda a: (label_rank[a["label"]], a["years"]))

    return {
        "property_id": property_id, "title": prop["title"], "cash_price": prop["cash_price"],
        "plans": sorted(assessed, key=lambda a: a["years"]),
        "recommended_plan_years": best["years"] if best else None,
        "recommended_reason": (
            f"{best['years']}-year plan is the best fit — {best['reason']}" if best and best["label"] in ("GREEN", "YELLOW")
            else (f"No plan on this property comfortably fits your stated constraints; closest is the "
                  f"{best['years']}-year plan, but {best['reason'].lower()}" if best else
                  "No installment plans available for this property — likely a cash-only listing.")
        ),
    }


def get_affordability_profile(lead_id: int) -> dict:
    """A general orientation, not tied to one property: given this lead's
    stated constraints, what does their actual affordable range look like?
    Deliberately conservative — only states what's directly derivable from
    their own stated numbers, never estimates a plan structure they haven't
    given us (no assumed years/down-payment-% beyond what's on file)."""
    conn = get_connection()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        conn.close()
        raise ValueError(f"Lead {lead_id} not found")
    lead = dict(lead)

    # Real, observed installment terms across the current inventory (not invented) —
    # used only to give a rough total-price ballpark from a monthly-payment constraint,
    # clearly labeled as an estimate derived from real market data, not a promise.
    row = conn.execute("""
        SELECT AVG(years) as avg_years, AVG(down_payment_pct) as avg_down_pct
        FROM installment_plans
    """).fetchone()
    conn.close()
    avg_years = row["avg_years"] or 7
    avg_down_pct = row["avg_down_pct"] or 10

    summary_bits = []
    estimated_max_total_price = None

    if lead["payment_preference"] == "cash":
        if lead["budget_max"]:
            summary_bits.append(f"Cash buyer with a stated budget up to {lead['budget_max']:,.0f} EGP.")
        else:
            summary_bits.append("Cash buyer — no specific budget ceiling stated yet.")
    elif lead["payment_preference"] == "installment":
        if lead["max_monthly_payment"]:
            # Rough back-of-envelope estimate from real average plan terms, explicitly
            # labeled as an estimate — never presented as a guarantee or a real quote.
            monthly_budget_over_term = lead["max_monthly_payment"] * avg_years * 12
            down_estimate = monthly_budget_over_term * (avg_down_pct / 100) / (1 - avg_down_pct / 100)
            estimated_max_total_price = round(monthly_budget_over_term + down_estimate, -4)
            summary_bits.append(
                f"Installment buyer with a stated monthly cap of {lead['max_monthly_payment']:,.0f} EGP. "
                f"Based on typical current market terms (~{avg_years:.0f}-year plans, "
                f"~{avg_down_pct:.0f}% down payment), that ballparks to roughly "
                f"{estimated_max_total_price:,.0f} EGP total price — an estimate from real "
                f"average plan terms, not a guaranteed figure."
            )
        else:
            summary_bits.append("Installment buyer — no stated monthly-payment cap yet, "
                                "so no price estimate can be derived.")
        if lead["max_down_payment"]:
            summary_bits.append(f"Stated down-payment ceiling: {lead['max_down_payment']:,.0f} EGP.")
    else:
        summary_bits.append("Payment preference not yet stated — affordability can't be assessed "
                            "until cash vs. installment is known.")

    return {
        "lead_id": lead_id,
        "payment_preference": lead["payment_preference"],
        "estimated_max_total_price": estimated_max_total_price,
        "summary": " ".join(summary_bits),
        "based_on_market_averages": {"avg_years": round(avg_years, 1), "avg_down_payment_pct": round(avg_down_pct, 1)},
    }


if __name__ == "__main__":
    conn = get_connection()
    lead_id = conn.execute("SELECT id FROM leads LIMIT 1").fetchone()["id"]
    prop_id = conn.execute("""
        SELECT p.id FROM properties p JOIN installment_plans ip ON ip.property_id = p.id LIMIT 1
    """).fetchone()["id"]
    conn.close()

    print("=== Affordability profile ===")
    profile = get_affordability_profile(lead_id)
    print(profile["summary"])
    print()

    print("=== Plan comparison for one property ===")
    comparison = compare_property_plans(prop_id, lead_id)
    print(f"{comparison['title']} — {comparison['cash_price']:,.0f} EGP cash")
    for p in comparison["plans"]:
        print(f"  [{p['label']:7s}] {p['years']}yr — {p['monthly_payment']:,.0f} EGP/mo, "
              f"{p['down_payment']:,.0f} EGP down — {p['reason']}")
    print(f"\nRecommendation: {comparison['recommended_reason']}")
