"""
Phase 2 — Smart Next Best Action.

Upgrades Phase 4's original single-signal (score threshold) recommendation
into a multi-signal, explainable decision engine. Reuses existing modules
rather than duplicating them: matching.py for property recommendations,
scoring.py for the lead score, extraction.py for missing-field/objection
data, feedback.py for model reliability. No second matching algorithm, no
new ML model — this is a deterministic rules layer over data that already
exists, exactly as instructed.

Backward compatibility: generate_next_action(lead_id) keeps its original
signature and still writes to next_actions exactly as before. The response
dict is EXTENDED, not restructured — action_type/channel/urgency/
message_suggestion/reasoning are still present (message_suggestion and
reasoning now alias suggested_message/reason so existing frontend code
that reads them keeps working), with new fields added alongside.

Deliberate, documented behavior change: action_type's vocabulary is
upgraded from the original 4 values (call/whatsapp/wait/send_properties)
to the 8-action controlled set the spec requires (call_now,
send_matching_properties, follow_up, ask_clarifying_question,
schedule_viewing, send_installment_options, re_engage, no_action). This
changes what STRING VALUES the API returns for action_type, not the field
name or endpoint. The dashboard (the only consumer) is updated to match.
"""
import json
import os
import re
from datetime import datetime

from db import get_connection
from matching import match_lead_to_properties
from scoring import score_lead, humanize_factors
from extraction import compute_missing_fields, suggest_next_question
from feedback import get_model_maturity

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

ACTION_TYPES = (
    "call_now", "send_matching_properties", "follow_up", "ask_clarifying_question",
    "schedule_viewing", "send_installment_options", "re_engage", "no_action",
)

# ---------------------------------------------------------------------------
# Signal gathering — reads existing tables, computes nothing that isn't
# already stored. This is descriptive statistics for rule-based decisions,
# not a duplicate of scoring.py's ML feature vector (different purpose,
# same source tables — that's expected reuse, not duplication).
# ---------------------------------------------------------------------------
def gather_engagement_signals(lead_id: int, conn) -> dict:
    events = [dict(r) for r in conn.execute(
        "SELECT * FROM lead_events WHERE lead_id = ? ORDER BY created_at DESC", (lead_id,)
    ).fetchall()]
    num_property_views = sum(1 for e in events if e["event_type"] == "property_viewed")
    num_viewing_requests = sum(1 for e in events if e["event_type"] == "viewing_booked")

    # System-generated bookkeeping events (this engine's own recommendation log) must
    # NOT count as customer/agent activity — otherwise simply generating a
    # recommendation would make a stale lead look freshly active on the very next
    # call, masking the exact staleness signal re_engage depends on. Real bug, found
    # by testing: the priority queue and a direct per-lead call disagreed for the
    # same lead because the first call's own logged event changed the second call's
    # recency calculation.
    activity_events = [e for e in events if not e["event_type"].startswith("next_action_")]
    days_since_last_activity = None
    if activity_events:
        last = datetime.fromisoformat(activity_events[0]["created_at"])
        days_since_last_activity = max((datetime.now() - last).total_seconds() / 86400, 0)

    return {
        "num_events": len(events),
        "num_activity_events": len(activity_events),
        "num_property_views": num_property_views,
        "num_viewing_requests": num_viewing_requests,
        "days_since_last_activity": days_since_last_activity,
    }


def _plan_fit_reason(top_match):
    """Pulls the real plan-fit sentence matching.py already generated for the
    top match, instead of re-describing or fabricating payment figures."""
    if not top_match:
        return None
    for r in top_match.get("reasons", []):
        if "plan" in r.lower() or "cash price" in r.lower():
            return r
    return None


_PLAN_FITS_RE = re.compile(r"(\d+)-year plan fits: ([\d,]+) EGP/month, ([\d,]+) EGP down payment", re.I)


def describe_plan_reason(plan_reason):
    """Turns matching.py's "10-year plan fits: 60,000 EGP/month, 800,000 EGP down payment" into
    "a 10-year plan at 60,000 EGP/month with 800,000 EGP down payment", which reads naturally in a
    sentence. Returns None unless the text really is a plan that FITS: a "Closest plan is ..." reason
    describes a plan that does not fit and must never be presented as one that works."""
    m = _PLAN_FITS_RE.search(plan_reason or "")
    if not m:
        return None
    years, monthly, down = m.groups()
    return f"a {years}-year plan at {monthly} EGP/month with {down} EGP down payment"


# ---------------------------------------------------------------------------
# Decision engine — deterministic, explainable. Every branch below returns
# a `checklist` of the real signals that justified it (section 10: "WHY?").
# ---------------------------------------------------------------------------
def decide_action(lead: dict, score_result: dict, matches: list, missing_fields: list,
                   objections: list, engagement: dict) -> dict:
    score = score_result["score"]
    status = lead.get("status")
    top_match = matches[0] if matches else None

    checklist = []
    if lead.get("intent") == "high":
        checklist.append("High stated intent")
    if lead.get("urgency") == "high":
        checklist.append("Expressed high urgency")
    if engagement["num_property_views"] > 0:
        plural = "y" if engagement["num_property_views"] == 1 else "ies"
        checklist.append(f"Viewed {engagement['num_property_views']} propert{plural}")
    if engagement["num_viewing_requests"] > 0:
        checklist.append("Requested or booked a viewing")
    if top_match and top_match["score"] >= 80:
        checklist.append(f"Strong inventory match ({top_match['score']}%)")
    if engagement["days_since_last_activity"] is not None and engagement["days_since_last_activity"] <= 1:
        checklist.append("Activity within the last 24 hours")
    if score >= 80:
        checklist.append(f"High lead score ({score})")

    # 1. Deal already closed — nothing to recommend
    if status in ("won", "lost"):
        return {
            "action": "no_action", "priority": "none",
            "reason": f"Lead is already marked {status} — no further action needed.",
            "checklist": [f"Status: {status}"],
        }

    # 2. Gone quiet after prior engagement — don't keep pushing a cold lead
    if (engagement["num_activity_events"] > 0 and engagement["days_since_last_activity"] is not None
            and engagement["days_since_last_activity"] >= 14 and status not in ("viewing_booked", "negotiating")):
        checklist.append(f"No activity in {int(engagement['days_since_last_activity'])} days")
        return {
            "action": "re_engage", "priority": "low",
            "reason": "No recent activity — re-engage before investing more agent time on this lead.",
            "checklist": checklist,
        }

    # 3. Payment objection — address it with real plan data, not a generic nudge
    if "monthly_payment" in objections or "down_payment" in objections:
        which = "monthly payment" if "monthly_payment" in objections else "down payment"
        checklist.append(f"Raised a {which} objection")
        return {
            "action": "send_installment_options", "priority": "high" if score >= 55 else "medium",
            "reason": f"Lead raised a {which} concern — address it directly with concrete plan options "
                      f"instead of a generic follow-up.",
            "checklist": checklist,
        }

    # 4. Viewing already booked vs. just requested
    if status == "viewing_booked":
        checklist.append("Viewing already booked")
        return {
            "action": "follow_up", "priority": "medium",
            "reason": "Viewing is booked — follow up to confirm attendance and gather feedback afterward.",
            "checklist": checklist,
        }
    if engagement["num_viewing_requests"] > 0:
        checklist.append("Requested a viewing")
        return {
            "action": "schedule_viewing", "priority": "high",
            "reason": "Lead requested a viewing — get it scheduled while interest is fresh.",
            "checklist": checklist,
        }

    # 5. Missing critical profile info on an otherwise worthwhile lead
    if missing_fields and score >= 40:
        checklist.append(f"Missing: {', '.join(missing_fields)}")
        return {
            "action": "ask_clarifying_question", "priority": "medium",
            "reason": "Key profile information is still missing — resolve it before pushing matches "
                      "that might not actually fit.",
            "checklist": checklist,
        }

    # 6. Strong all-around signal — call now
    if score >= 80 and top_match and top_match["score"] >= 70:
        return {
            "action": "call_now", "priority": "high",
            "reason": "High conversion probability, strong recent engagement, and a strong inventory "
                      "match — a live call converts faster than text at this stage.",
            "checklist": checklist,
        }

    # 7. Moderate score with usable matches — keep momentum without overcommitting agent time
    if score >= 55 and matches:
        return {
            "action": "send_matching_properties", "priority": "medium",
            "reason": "Solid potential lead with usable inventory matches — send options to gauge "
                      "real interest before committing to a call.",
            "checklist": checklist,
        }

    # 8. Low score fallback — never fabricate urgency that isn't there
    if matches:
        return {
            "action": "send_matching_properties", "priority": "low",
            "reason": "Lower engagement/conversion signals — worth sending options, but not yet "
                      "worth a call or heavy agent time.",
            "checklist": checklist,
        }
    return {
        "action": "no_action", "priority": "low",
        "reason": "Low engagement and no strong inventory match currently available — nothing "
                  "actionable to recommend right now.",
        "checklist": checklist,
    }


def compute_confidence(decision: dict, missing_fields_count: int) -> dict:
    """Recommendation confidence — deliberately SEPARATE from model_reliability
    (see feedback.get_model_maturity). This measures how clear-cut the RULE
    that fired is (how many corroborating signals it had), not how much real
    outcome data has trained the underlying scoring model. Do not conflate
    the two — a confident rule can still sit on top of an unreliable model,
    and the API/UI must show both, not blend them into one number."""
    if decision["action"] == "no_action" and decision["priority"] == "none":
        return {"confidence": "N/A", "confidence_reason": "Lead is already closed."}

    strength = len(decision["checklist"]) - missing_fields_count * 0.5
    if strength >= 3:
        return {"confidence": "High",
                "confidence_reason": f"Based on {len(decision['checklist'])} corroborating signal(s)."}
    if strength >= 1:
        return {"confidence": "Medium",
                "confidence_reason": f"Based on {len(decision['checklist'])} signal(s), with some profile gaps remaining."}
    return {"confidence": "Low",
            "confidence_reason": "Limited signal strength — treat as a starting suggestion, not a strong recommendation."}


def build_talking_points(lead: dict, matches: list, objections: list) -> list:
    """Every point here is derived from a real stored field or a real match
    reason — never an invented figure or claim."""
    points = []
    if "monthly_payment" in objections:
        points.append("Lead is sensitive to monthly payment — lead with the lowest-monthly-payment plan available.")
    if "down_payment" in objections:
        points.append("Lead is concerned about down payment size — highlight lower-down-payment options.")
    if lead.get("preferred_locations"):
        points.append(f"Prioritize properties in {lead['preferred_locations']}.")
    if lead.get("max_monthly_payment"):
        points.append(f"Avoid showing units above {int(lead['max_monthly_payment']):,} EGP/month.")
    if lead.get("delivery_preference"):
        points.append(f"Lead prefers {lead['delivery_preference'].replace('_', ' ')} delivery.")
    plan_reason = _plan_fit_reason(matches[0] if matches else None)
    if plan_reason:
        points.append(f"On {matches[0]['title']}: {plan_reason}")
    if not points:
        points.append("No specific objections or preferences recorded yet — focus on understanding requirements.")
    return points


def build_question_to_ask(decision: dict, lead: dict) -> str:
    if decision["action"] == "ask_clarifying_question":
        return suggest_next_question(lead)
    if decision["action"] == "send_installment_options":
        return "Would a lower down payment with a longer installment period work better for you?"
    return None


def build_suggested_message(lead: dict, decision: dict, top_match: dict, question: str) -> str:
    name_first = (lead.get("name") or "there").split(" ")[0]
    action = decision["action"]

    if action == "no_action":
        return None
    if action == "re_engage":
        return (f"Hi {name_first}, following up to see if you're still looking — happy to send "
                f"fresh options if your requirements have changed.")
    if action == "send_installment_options" and top_match:
        plan_text = describe_plan_reason(_plan_fit_reason(top_match))
        base = f"Hi {name_first}, I hear you on the payment — "
        base += f"I found a plan that works better: {plan_text}. " if plan_text else "let me find a plan that fits better. "
        if question:
            base += question
        return base
    if action == "ask_clarifying_question" and question:
        return f"Hi {name_first}! {question}"
    if action in ("schedule_viewing", "follow_up"):
        return f"Hi {name_first}, let's get a viewing time locked in — what does your schedule look like this week?"
    if top_match:
        prop_line = (f"Hi {name_first}! I found a {top_match['bedrooms']}-bedroom "
                     f"{top_match['title'].split(' in ')[0].lower()} in {top_match['compound']}, "
                     f"{top_match['location']} that matches what you're looking for")
        plan_text = describe_plan_reason(_plan_fit_reason(top_match))
        prop_line += f" — it comes with {plan_text}." if plan_text else "."
        return prop_line + " Want me to send more details or set up a viewing?"
    return (f"Hi {name_first}! Still looking for the right fit for you — could you tell me a bit "
            f"more about your must-haves (location, budget range, bedrooms)?")


# ---------------------------------------------------------------------------
# LLM path (optional enhancement, not required — see module docstring)
# ---------------------------------------------------------------------------
NBA_PROMPT_TEMPLATE = """You are a sales operations assistant for an Egyptian real estate brokerage.
Given this lead's profile, score, engagement signals, and top property matches, recommend the single
best next action. Return ONLY a JSON object:

{{
  "action": "call_now" | "send_matching_properties" | "follow_up" | "ask_clarifying_question" |
      "schedule_viewing" | "send_installment_options" | "re_engage" | "no_action",
  "priority": "high" | "medium" | "low" | "none",
  "reason": "one sentence explaining why, referencing only the data provided below",
  "talking_points": ["short bullet points for the agent, from the data provided"],
  "question_to_ask": "a specific question if the action calls for one, else null",
  "suggested_message": "a short, natural WhatsApp-style message, referencing only real data below"
}}

Never invent property details, prices, or plan terms not present in the data below.

Lead profile: {lead_json}
Lead score: {score}/100 (conversion probability {prob})
Engagement: {engagement_json}
Missing fields: {missing_fields}
Objections: {objections}
Top property matches: {matches_json}
"""


def generate_with_llm(lead, score_result, matches, engagement, missing_fields, objections) -> dict:
    import urllib.request
    prompt = NBA_PROMPT_TEMPLATE.format(
        lead_json=json.dumps(lead, default=str, indent=2),
        score=score_result["score"], prob=score_result["conversion_probability"],
        engagement_json=json.dumps(engagement), missing_fields=missing_fields, objections=objections,
        matches_json=json.dumps(matches[:3], indent=2) if matches else "No matches found.",
    )
    body = {"model": "claude-sonnet-4-6", "max_tokens": 500,
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    text = "".join(b["text"] for b in data["content"] if b["type"] == "text")
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    result = json.loads(text)
    result.setdefault("checklist", [])
    return result


# ---------------------------------------------------------------------------
# Public entry point — signature unchanged from Phase 4 for API compatibility
# ---------------------------------------------------------------------------
def generate_next_action(lead_id: int) -> dict:
    conn = get_connection()
    lead_row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead_row:
        conn.close()
        raise ValueError(f"Lead {lead_id} not found")
    lead = dict(lead_row)
    engagement = gather_engagement_signals(lead_id, conn)
    real_outcome_count = conn.execute("SELECT COUNT(*) c FROM outcomes").fetchone()["c"]
    conn.close()

    score_result = score_lead(lead_id)
    matches = match_lead_to_properties(lead_id, top_n=3)
    top_match = matches[0] if matches else None
    missing_fields = compute_missing_fields(lead)
    objections = json.loads(lead["objections"]) if lead.get("objections") else []
    maturity = get_model_maturity(real_outcome_count)

    generated_by = "rule_engine"
    if ANTHROPIC_API_KEY:
        try:
            decision = generate_with_llm(lead, score_result, matches, engagement, missing_fields, objections)
            generated_by = "llm"
        except Exception as e:
            print(f"[next_action] LLM generation failed ({e}), falling back to rules")
            decision = decide_action(lead, score_result, matches, missing_fields, objections, engagement)
    else:
        decision = decide_action(lead, score_result, matches, missing_fields, objections, engagement)

    confidence = compute_confidence(decision, len(missing_fields))
    question = decision.get("question_to_ask") or build_question_to_ask(decision, lead)
    talking_points = decision.get("talking_points") or build_talking_points(lead, matches, objections)
    suggested_message = decision.get("suggested_message") or build_suggested_message(lead, decision, top_match, question)
    recommended_property_ids = [m["property_id"] for m in matches[:3]] if decision["action"] not in (
        "no_action", "re_engage", "ask_clarifying_question") else []

    # legacy-compatible urgency bucket, derived from priority so old frontend code
    # (or anyone still reading `urgency`) keeps working without change
    urgency_map = {"high": "now", "medium": "today", "low": "this_week", "none": "low_priority"}
    urgency = urgency_map.get(decision["priority"], "today")
    channel = "call" if decision["action"] == "call_now" else (
        "whatsapp" if decision["action"] != "no_action" else "none")

    conn = get_connection()
    conn.execute("""
        INSERT INTO next_actions (lead_id, action_type, channel, message_suggestion, reasoning, urgency, generated_by)
        VALUES (?,?,?,?,?,?,?)
    """, (lead_id, decision["action"], channel, suggested_message, decision["reason"], urgency, generated_by))
    # Action tracking (section 13): logged as a lead_event too, distinct from the
    # next_actions history row above, so it participates in engagement counts and
    # future feedback analysis ("which recommended actions actually produce wins?")
    conn.execute("""
        INSERT INTO lead_events (lead_id, event_type, payload) VALUES (?, 'next_action_generated', ?)
    """, (lead_id, f"{decision['action']}: {decision['reason']}"))
    conn.commit()
    conn.close()

    return {
        # new Phase 2 fields
        "action": decision["action"], "priority": decision["priority"], "reason": decision["reason"],
        "checklist": decision["checklist"], "recommended_property_ids": recommended_property_ids,
        "talking_points": talking_points, "question_to_ask": question,
        "suggested_message": suggested_message, "confidence": confidence["confidence"],
        "confidence_reason": confidence["confidence_reason"],
        "model_reliability": maturity["reliability"], "model_maturity": maturity["maturity"],
        # legacy-compatible fields (existing frontend/tests reading these keep working)
        "action_type": decision["action"], "channel": channel, "urgency": urgency,
        "message_suggestion": suggested_message, "reasoning": decision["reason"],
        # unchanged from Phase 4
        "lead_id": lead_id, "score": score_result["score"],
        "conversion_probability": score_result["conversion_probability"],
        "top_match": top_match, "matches": matches, "generated_by": generated_by,
    }


def track_next_action_event(lead_id: int, event: str, note: str = ""):
    """event: 'selected' | 'completed' — agent interaction with a recommendation,
    for future analysis of which recommended actions actually produce results."""
    if event not in ("selected", "completed"):
        raise ValueError("event must be 'selected' or 'completed'")
    conn = get_connection()
    conn.execute("""
        INSERT INTO lead_events (lead_id, event_type, payload) VALUES (?, ?, ?)
    """, (lead_id, f"next_action_{event}", note))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Priority queue — section 9. Reuses generate_next_action per active lead
# rather than a separate scoring path.
# ---------------------------------------------------------------------------
PRIORITY_WEIGHT = {"high": 1.0, "medium": 0.6, "low": 0.3, "none": 0.0}


def get_priority_queue(limit: int = 20) -> list:
    conn = get_connection()
    active_leads = conn.execute(
        "SELECT id FROM leads WHERE status NOT IN ('won', 'lost') ORDER BY id"
    ).fetchall()
    conn.close()

    queue = []
    for row in active_leads:
        result = generate_next_action(row["id"])
        priority_score = round(result["score"] * PRIORITY_WEIGHT.get(result["priority"], 0.3), 1)
        queue.append({
            "lead_id": result["lead_id"], "score": result["score"],
            "priority": result["priority"], "priority_score": priority_score,
            "action": result["action"], "reason": result["reason"],
            "confidence": result["confidence"],
        })

    conn = get_connection()
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM leads")}
    conn.close()
    for item in queue:
        item["name"] = names.get(item["lead_id"], "?")

    queue.sort(key=lambda x: x["priority_score"], reverse=True)
    return queue[:limit]


if __name__ == "__main__":
    conn = get_connection()
    ids = [r["id"] for r in conn.execute("SELECT id FROM leads").fetchall()]
    conn.close()
    for lid in ids:
        r = generate_next_action(lid)
        print(f"\nLead {lid} — score {r['score']} — action: {r['action'].upper()} (priority: {r['priority']}, confidence: {r['confidence']})")
        print(f"   why: {r['reason']}")
        for c in r["checklist"]:
            print(f"     ✓ {c}")
        if r["suggested_message"]:
            print(f"   msg: {r['suggested_message']}")

    print("\n=== PRIORITY QUEUE ===")
    for item in get_priority_queue():
        print(f"{item['priority_score']:5.1f}  {item['name']:20s} {item['action']:28s} {item['reason'][:60]}")