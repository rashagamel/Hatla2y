"""Regression tests for the pre-demo clean-up: consistent unit counts and a readable reply sentence.
Pure functions only (no database)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from conversation import _describe_plan, build_conversation_reply  # noqa: E402
from matching import evaluate_property  # noqa: E402
from scoring import _summarize_fit  # noqa: E402


def lead(**kw):
    base = {"payment_preference": "installment", "budget_min": 7_000_000, "budget_max": 9_000_000,
            "max_monthly_payment": 90_000, "max_down_payment": 900_000,
            "preferred_locations": "New Cairo", "min_bedrooms": 3}
    base.update(kw)
    return base


def prop(pid, price=8_000_000, location="New Cairo", beds=3):
    return {"id": pid, "title": f"P{pid}", "compound": "C", "location": location, "cash_price": price,
            "bedrooms": beds, "finishing": "fully-finished", "has_parking": 1, "has_security": 1}


def plan(monthly, total=8_000_000, down_pct=10.0):
    return {"years": 10, "monthly_payment": monthly, "down_payment_pct": down_pct, "total_price": total}


def scored(l):
    units = [
        (prop(1), [plan(60_000)]),                                  # meets everything
        (prop(2), [plan(60_000)]),                                  # meets everything
        (prop(3, beds=2), [plan(60_000)]),                          # right area/budget/plan, too few bedrooms
        (prop(4, location="Maadi"), [plan(60_000)]),                # wrong area
        (prop(5, price=8_500_000), [plan(150_000, 8_500_000)]),     # plan too expensive
        (prop(6, price=4_500_000), [plan(30_000, 4_500_000)]),      # far below budget
    ]
    return [evaluate_property(p, pl, l) for p, pl in units]


def test_unit_counts_form_one_funnel_that_never_grows():
    fit = _summarize_fit(scored(lead()), lead())
    assert fit["location_count"] >= fit["budget_count"] >= fit["plan_count"] >= fit["qualified_count"]


def test_final_matching_units_equals_units_with_a_fitting_plan_for_an_installment_buyer():
    fit = _summarize_fit(scored(lead()), lead())
    assert fit["qualified_count"] == 2
    assert fit["plan_count"] == fit["qualified_count"]      # the old bug showed 257 vs 136 for this


def test_units_with_too_few_bedrooms_are_not_counted_as_fitting_the_budget():
    fit = _summarize_fit(scored(lead()), lead())
    # P1, P2 and P5 are priced inside the budget; P3 (2 bedrooms) is excluded even though its price fits
    assert fit["budget_count"] == 3


def test_describe_plan_reads_naturally():
    text = _describe_plan("12-year plan fits: 52,800 EGP/month, 400,000 EGP down payment")
    assert text == "a 12-year plan at 52,800 EGP/month with 400,000 EGP down payment"


def test_describe_plan_returns_none_for_unexpected_text():
    assert _describe_plan("Closest plan is 7 years at 48,200 EGP/month") is None
    assert _describe_plan(None) is None


def test_reply_sentence_has_no_garbled_grammar():
    l = {"name": "Test Client", "preferred_locations": "New Cairo", "max_monthly_payment": 70_000}
    extracted = {"objections": ["monthly_payment"], "removed_locations": []}
    matches = [{"title": "Apartment in Taj City", "location": "New Cairo", "hard_fail": [], "warnings": [],
                "reasons": ["12-year plan fits: 52,800 EGP/month, 400,000 EGP down payment"]}]
    reply = build_conversation_reply(l, extracted, matches)
    assert "has a 12-year plan at 52,800 EGP/month with 400,000 EGP down payment" in reply
    assert "plan fits" not in reply and "egp" not in reply     # no lower-cased currency, no raw reason text


# --- next_action.py: the suggested WhatsApp message --------------------------------------------
from next_action import build_suggested_message, describe_plan_reason  # noqa: E402


def _top(reason, **kw):
    m = {"title": "Apartment in Taj City", "compound": "Taj City", "location": "New Cairo", "bedrooms": 3,
         "reasons": [reason], "warnings": [], "hard_fail": []}
    m.update(kw)
    return m


def test_installment_message_has_natural_wording():
    top = _top("12-year plan fits: 52,800 EGP/month, 400,000 EGP down payment")
    msg = build_suggested_message({"name": "Test Client"}, {"action": "send_installment_options"}, top, "Q?")
    assert "a 12-year plan at 52,800 EGP/month with 400,000 EGP down payment" in msg
    assert "egp" not in msg and "plan fits" not in msg


def test_a_plan_that_does_not_fit_is_never_presented_as_one_that_works():
    top = _top("Closest plan is 7 years at 48,200 EGP/month (~8,200 EGP/month above your stated max)")
    msg = build_suggested_message({"name": "Test Client"}, {"action": "send_installment_options"}, top, None)
    assert "48,200" not in msg
    assert "let me find a plan that fits better" in msg
    assert describe_plan_reason(top["reasons"][0]) is None


def test_property_message_is_not_lowercased_into_nonsense():
    top = _top("Price 8,000,000 EGP is within your budget range")
    msg = build_suggested_message({"name": "Test Client"}, {"action": "send_matching_properties"}, top, None)
    assert "price 8,000,000 egp" not in msg
    assert msg.endswith("Want me to send more details or set up a viewing?")