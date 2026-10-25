"""
Phase 2 — Conversation extraction.

Turns a raw WhatsApp-style message into structured lead profile fields:
  budget_min, budget_max, payment_preference, max_monthly_payment,
  max_down_payment, preferred_locations, min_bedrooms, purpose, purchase_timeline

Two extraction backends:
  1. LLM extraction via the Anthropic API (used automatically if ANTHROPIC_API_KEY
     is set in the environment) — this is the real production path.
  2. Heuristic regex/keyword extractor — used as a fallback so the pipeline is
     fully testable without API credentials. Handles English, common Arabizi
     numerals ("5M", "5 million", "خمسة مليون" is NOT covered — Arabic-script
     parsing would need the LLM path), and common Egyptian real-estate phrasing.

Both return the same shape, so `matching.py` and everything downstream never
needs to know which backend produced the profile.
"""
import os
import re
import json
from db import get_connection

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

EXTRACTION_SCHEMA_PROMPT = """You are extracting structured real-estate buyer requirements from a
customer's WhatsApp message (Egyptian real estate market). Return ONLY a JSON object, no other text,
with these fields (use null for anything not mentioned or not inferable):

{
  "budget_min": number or null,
  "budget_max": number or null,
  "payment_preference": "cash" | "installment" | null,
  "max_monthly_payment": number or null,
  "max_down_payment": number or null,
  "preferred_locations": array of strings (e.g. ["New Cairo", "6th of October"]) or [],
  "min_bedrooms": integer or null,
  "purpose": "family" | "investment" | "rental" | null,
  "purchase_timeline": "immediate" | "1-3m" | "3-6m" | "6m+" | null,
  "urgency": "low" | "medium" | "high" | null,
  "delivery_preference": "ready" | "under_construction" | null,
  "objections": array of strings from this fixed set only: ["monthly_payment", "down_payment",
      "price", "location", "delivery_timeline"] — only include ones actually expressed as a
      concern/hesitation in the message, or [],
  "removed_locations": array of strings — locations the customer explicitly said they do NOT
      want anymore (e.g. "I don't like Sheikh Zayed"), using only the location names listed
      below, or [],
  "engagement_signal": "still_interested" | "losing_interest" | null — "losing_interest" only if
      the message clearly signals dropping out (e.g. "not interested anymore", "please cancel"),
      "still_interested" if they raised an objection or preference change but are clearly still
      engaged, null if there's no clear signal either way,
  "field_confidence": an object mapping each non-null field above to "explicit" (directly stated)
      or "inferred" (you derived it from context/implication, not a direct statement) — e.g.
      {"budget_max": "explicit", "urgency": "inferred"}
}

Amounts are in EGP. "5M" or "5 million" or "5 مليون" means 5,000,000.

Recognized Egyptian location names (use EXACTLY these strings in preferred_locations):
  New Cairo        (التجمع الخامس, التجمع, New Cairo, 5th Settlement)
  6th of October   (أكتوبر, 6 أكتوبر, October, 6 October)
  Sheikh Zayed     (الشيخ زايد, Sheikh Zayed, New Zayed, زايد الجديدة)
  North Coast      (الساحل الشمالي, الساحل, North Coast, Sahel, Ras El Hekma, رأس الحكمة)
  New Capital      (العاصمة الإدارية, العاصمة, New Capital, New Admin Capital)
  Mostakbal City   (مدينة المستقبل, مستقبل, Mostakbal)
  El Shorouk       (الشروق, Shorouk)
  Madinaty         (مدينتي, Madinaty)
  New Giza         (نيو جيزة, New Giza)
  Ain Sokhna       (العين السخنة, السخنة, Sokhna, Ain Sokhna)
  Red Sea          (البحر الأحمر, Hurghada, الغردقة, Gouna, الجونة, Soma Bay)
  Nasr City        (مدينة نصر, Nasr City)
  Maadi            (المعادي, Maadi)
  Alexandria       (الإسكندرية, Alexandria, Alex)
  Giza             (الجيزة, Dokki, الدقي, Mohandessin, المهندسين)

Do not invent information that isn't stated or clearly implied — if genuinely unclear, use null.

Message:
"""


def extract_with_llm(message_text: str) -> dict:
    """Calls the Anthropic API to extract structured fields. Requires ANTHROPIC_API_KEY."""
    import urllib.request

    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 500,
        "messages": [{"role": "user", "content": EXTRACTION_SCHEMA_PROMPT + message_text}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    text = "".join(b["text"] for b in data["content"] if b["type"] == "text")
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# Heuristic fallback (no API key needed) — regex/keyword based
# ---------------------------------------------------------------------------
KNOWN_LOCATIONS = [
    "New Cairo", "6th of October", "6 October", "Sheikh Zayed", "North Coast",
    "New Capital", "Mostakbal City", "El Shorouk", "Madinaty", "New Giza",
    "Ain Sokhna", "Red Sea", "Nasr City", "Maadi", "Zamalek",
    "Alexandria", "Giza",
]
LOCATION_ALIASES = {
    "6 october": "6th of October", "october": "6th of October",
    "zayed": "Sheikh Zayed", "sheikh zayed": "Sheikh Zayed",
    "north coast": "North Coast", "sahel": "North Coast",
    "new capital": "New Capital",
    "mostakbal": "Mostakbal City",
    "shorouk": "El Shorouk", "el shorouk": "El Shorouk",
    "madinaty": "Madinaty",
    "new giza": "New Giza",
    "sokhna": "Ain Sokhna", "ain sokhna": "Ain Sokhna",
    "hurghada": "Red Sea", "gouna": "Red Sea", "red sea": "Red Sea",
    "soma bay": "Red Sea", "makadi": "Red Sea",
    "nasr city": "Nasr City",
    "maadi": "Maadi",
    "zamalek": "Zamalek",
    "alex": "Alexandria", "alexandria": "Alexandria",
    "dokki": "Giza", "mohandessin": "Giza", "giza": "Giza",
}

# Arabic support — real Arabic-script location names, ordered longest-first so
# a more specific phrase ("التجمع الخامس") is checked before a shorter one that
# could otherwise match as a false partial ("التجمع" alone is also used loosely
# for New Cairo generally, so order doesn't change the outcome here, but the
# principle matters for phrases like "الشيخ زايد" containing "زايد").
LOCATION_AR_MAP = [
    ("التجمع الخامس", "New Cairo"), ("التجمع الأول", "New Cairo"), ("التجمع", "New Cairo"),
    ("القاهره الجديده", "New Cairo"), ("القاهرة الجديدة", "New Cairo"),
    ("الشيخ زايد", "Sheikh Zayed"), ("زايد الجديدة", "Sheikh Zayed"), ("زايد", "Sheikh Zayed"),
    ("الساحل الشمالي", "North Coast"), ("رأس الحكمة", "North Coast"), ("الساحل", "North Coast"),
    ("6 أكتوبر", "6th of October"), ("أكتوبر", "6th of October"),
    ("العاصمة الإدارية", "New Capital"), ("العاصمة الجديدة", "New Capital"), ("العاصمة", "New Capital"),
    ("مدينة المستقبل", "Mostakbal City"), ("المستقبل", "Mostakbal City"),
    ("الشروق", "El Shorouk"),
    ("مدينتي", "Madinaty"),
    ("نيو جيزة", "New Giza"),
    ("العين السخنة", "Ain Sokhna"), ("السخنة", "Ain Sokhna"),
    ("البحر الأحمر", "Red Sea"), ("الغردقة", "Red Sea"), ("الجونة", "Red Sea"),
    ("مصر الجديدة", "Heliopolis"),
    ("مدينة نصر", "Nasr City"),
    ("المعادي", "Maadi"),
    ("الزمالك", "Zamalek"),
    ("الإسكندرية", "Alexandria"), ("اسكندرية", "Alexandria"),
    ("الجيزة", "Giza"), ("الدقي", "Giza"), ("المهندسين", "Giza"),
]

# Arabic-Indic digits (used by some keyboards/users) -> Western digits, so every
# number regex below (all written for Western digits) works regardless of
# which numeral style the customer actually typed.
ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

AR_MILLION_WORDS = ["مليون", "ملايين"]
AR_THOUSAND_WORDS = ["الف", "ألف", "آلاف"]

# Phase 3 (Conversation Copilot): negative-preference detection — "I don't like
# Sheikh Zayed" should REMOVE a location, not just fail to add one. Previously
# preferred_locations was purely additive (see merge_profile_into_lead), so a
# customer ruling out an area had no effect on the stored profile at all.
NEGATION_CUE_PATTERNS = [
    r"don'?t (?:really |particularly |especially )?like",
    r"not (?:really |particularly |especially )?(?:keen on|interested in|a fan of|into)",
    r"no longer (?:really )?(?:want|interested in|considering)",
    r"rule out", r"doesn'?t work for me", r"won'?t work for me",
]

LOSING_INTEREST_CUES = [
    "not interested anymore", "not interested any more", "no longer looking",
    "found something else", "don't want to continue", "please cancel", "not looking anymore",
    "changed my mind about buying", "going with someone else",
]


def _detect_removed_locations(text):
    """Only removes a location if a negation cue appears in the same clause
    (within a short character window before the location mention, truncated
    at the last sentence boundary) — avoids false positives like "I don't
    like small kitchens. New Cairo works great" where the negation and the
    location are in different sentences entirely. A pure character-distance
    window without a sentence-boundary cutoff was found to falsely flag
    "New Cairo" in exactly that example during testing."""
    removed = []
    for loc in KNOWN_LOCATIONS:
        loc_l = loc.lower()
        canonical = "6th of October" if loc == "6 October" else loc
        for m in re.finditer(re.escape(loc_l), text):
            window = text[max(0, m.start() - 40):m.start()]
            # truncate at the last sentence-ending punctuation so an earlier,
            # unrelated sentence's negation cue can't leak into this clause
            last_boundary = max(window.rfind("."), window.rfind("!"), window.rfind("?"))
            if last_boundary != -1:
                window = window[last_boundary + 1:]
            if any(re.search(p, window) for p in NEGATION_CUE_PATTERNS):
                if canonical not in removed:
                    removed.append(canonical)
                break
    return removed


def _detect_engagement_signal(text, objections, removed_locations):
    """still_interested vs losing_interest — a lightweight, best-effort signal
    for whether an objection/preference change comes alongside continued
    engagement or an actual drop-off. Absence of either cue returns None
    (not everything needs a signal) rather than guessing."""
    if any(cue in text for cue in LOSING_INTEREST_CUES):
        return "losing_interest"
    if objections or removed_locations:
        # raising a specific objection or ruling something out is itself a sign
        # of continued, active engagement — they're refining, not disengaging
        return "still_interested"
    return None

MONEY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(m|million|k|thousand)?", re.IGNORECASE)


def _parse_money(text_fragment: str):
    text_fragment = text_fragment.replace(",", "")
    m = MONEY_RE.search(text_fragment)
    if not m:
        return None
    value = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if unit in ("m", "million"):
        value *= 1_000_000
    elif unit in ("k", "thousand"):
        value *= 1_000
    return value


OBJECTION_PATTERNS = {
    "monthly_payment": [r"too (?:high|much).{0,20}month", r"can'?t afford.{0,20}month",
                         r"monthly.{0,20}(?:too|expensive|a lot|high)"],
    "down_payment": [r"too (?:high|much).{0,20}down", r"can'?t afford.{0,20}down",
                      r"down payment.{0,20}(?:too|high|a lot)"],
    "price": [r"too expensive", r"price.{0,10}(?:too )?high", r"over (?:my|our) budget", r"can'?t afford it"],
    "location": [r"too far", r"not (?:the )?right (?:location|area)", r"far from (?:work|everything)"],
    "delivery_timeline": [r"too long.{0,10}(?:wait|deliver)", r"delivery.{0,15}(?:too|far|long|late)",
                           r"waiting.{0,15}too long"],
}


def _detect_objections(text):
    found = []
    for objection, patterns in OBJECTION_PATTERNS.items():
        if any(re.search(p, text) for p in patterns):
            found.append(objection)
    return found


def _detect_urgency(text, purchase_timeline):
    if any(w in text for w in ["asap", "urgent", "urgently", "right away", "immediately", "this week"]):
        return "high"
    if any(w in text for w in ["not in a rush", "no rush", "just looking", "just browsing", "someday", "no hurry"]):
        return "low"
    if purchase_timeline == "immediate":
        return "high"
    if purchase_timeline in ("1-3m",):
        return "medium"
    if purchase_timeline in ("3-6m", "6m+"):
        return "low"
    return "unknown"


def _detect_delivery_preference(text):
    if any(w in text for w in ["ready to move", "ready-to-move", "ready unit", "immediate delivery",
                                "move in now"]) or re.search(r"\bready\b", text):
        return "ready"
    if any(w in text for w in ["under construction", "off-plan", "off plan", "future delivery",
                                "can wait for delivery", "delivery in", "years to deliver"]):
        return "under_construction"
    return None


def _detect_arabic_locations(text):
    """Returns Arabic-script locations found, longest-phrase-first so a more
    specific match wins (checked in LOCATION_AR_MAP's defined order)."""
    found = []
    for phrase, canonical in LOCATION_AR_MAP:
        if phrase in text and canonical not in found:
            found.append(canonical)
    return found


def _parse_arabic_money(text):
    """Finds the first Arabic-script money mention: "<number> مليون" or
    "<number> الف". Real Egyptian conversations write numbers in Western
    digits even in otherwise fully Arabic-script text (confirmed against
    the actual labeled Arabic dataset), but Arabic-Indic digits are
    normalized first in case a user types those instead.

    Only used as a BUDGET fallback, so it explicitly excludes any "الف"/
    "مليون" mention that's actually a down-payment or monthly-payment
    figure (e.g. "مقدم 500 الف") — without this, a down-payment-only
    message with no budget statement at all would get that same number
    double-counted as the budget too. Found as a real bug testing against
    "...وبمقدم 500 الف" with no budget mentioned."""
    normalized = text.translate(ARABIC_INDIC_DIGITS)
    down_or_monthly_cues = ["مقدم", "قسط شهري", "القسط الشهري"]

    for word in AR_MILLION_WORDS:
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*" + word, normalized):
            window = normalized[max(0, m.start() - 15):m.start()]
            if not any(cue in window for cue in down_or_monthly_cues):
                return float(m.group(1)) * 1_000_000
    for word in AR_THOUSAND_WORDS:
        for m in re.finditer(r"(\d+(?:,\d+)?)\s*" + word, normalized):
            window = normalized[max(0, m.start() - 15):m.start()]
            if not any(cue in window for cue in down_or_monthly_cues):
                return float(m.group(1).replace(",", "")) * 1_000
    return None


def _detect_arabic_bedrooms(text):
    normalized = text.translate(ARABIC_INDIC_DIGITS)
    # Real phrasing varies in order: "3 غرف" (number then noun) and "عدد غرف 3"
    # (noun then number, as in "number of rooms: 3") are both common — checked
    # in that order since number-first is more common overall.
    # Also handles singulars (غرفة/غرفه) and common adjacent-key keyboard typos (عرف/عرفة).
    bedroom_nouns = r"(?:غرف|غرفة|غرفه|أوض|اوضة|اوض|عرف|عرفة|عرفه)"
    m = re.search(r"(\d+)\s*" + bedroom_nouns, normalized)
    if m:
        return int(m.group(1))
    m = re.search(bedroom_nouns + r"[^\d]{0,10}(\d+)", normalized)
    return int(m.group(1)) if m else None


def _detect_arabic_payment_preference(text):
    if any(w in text for w in ["قسط", "أقساط", "اقساط", "بالتقسيط", "تقسيط", "مقدم", "بمقدم"]):
        return "installment"
    if any(w in text for w in ["كاش", "نقدا", "نقداً", "كامل المبلغ"]):
        return "cash"
    return None


def _parse_arabic_down_payment(text):
    normalized = text.translate(ARABIC_INDIC_DIGITS)
    m = re.search(r"مقدم\s*(?:حوالي\s*)?(\d+(?:,\d+)?)\s*(?:" + "|".join(AR_THOUSAND_WORDS) + ")", normalized)
    if m:
        return float(m.group(1).replace(",", "")) * 1_000
    m = re.search(r"مقدم\s*(?:حوالي\s*)?(\d+(?:\.\d+)?)\s*(?:" + "|".join(AR_MILLION_WORDS) + ")", normalized)
    if m:
        return float(m.group(1)) * 1_000_000
    return None


def _parse_arabic_monthly_payment(text):
    normalized = text.translate(ARABIC_INDIC_DIGITS)
    m = re.search(r"(?:قسط شهري|القسط الشهري)[^\d]{0,20}(\d+(?:,\d+)?)\s*(?:"
                  + "|".join(AR_THOUSAND_WORDS) + ")", normalized)
    return float(m.group(1).replace(",", "")) * 1_000 if m else None


def _detect_arabic_purpose(text):
    if any(w in text for w in ["لسكني", "لسكن", "عيلتي", "أسرتي", "اسرتي"]):
        return "family"
    if any(w in text for w in ["استثمار", "استثماري"]):
        return "investment"
    return None


def _detect_arabic_timeline(text):
    if any(w in text for w in ["بسرعة", "عاجل", "الأسبوعين الجايين", "اسبوعين"]):
        return "immediate"
    if any(w in text for w in ["خلال شهر", "شهر لشهرين", "شهرين"]):
        return "1-3m"
    if any(w in text for w in ["3-6", "تلات شهور", "6 شهور"]):
        return "3-6m"
    if any(w in text for w in ["مش هاخد قرار قريب", "لسه بدور", "مش مستعجل", "مش هستعجل"]):
        return "6m+"
    return None


def extract_with_heuristics(message_text: str) -> dict:
    text = message_text.lower()
    result = {
        "budget_min": None, "budget_max": None, "payment_preference": None,
        "max_monthly_payment": None, "max_down_payment": None,
        "preferred_locations": [], "min_bedrooms": None,
        "purpose": None, "purchase_timeline": None,
        "urgency": None, "delivery_preference": None, "objections": [],
    }
    confidence = {}  # field -> "explicit" | "inferred", only for fields we actually set

    # payment preference:
    # Notice: "cash price" in real estate describes property sticker price (budget benchmark),
    # not buyer payment preference. If a buyer mentions monthly installments or down payment,
    # they want an installment plan.
    text_clean = re.sub(r"\bcash\s+price\b", "", text)
    has_no_installments = bool(re.search(r"\bno\s+installments?\b", text))
    has_installment_intent = (not has_no_installments) and (
        any(w in text for w in ["installment", "installments", "monthly", "قسط", "أقساط", "اقساط", "بالتقسيط", "تقسيط", "down payment", "مقدم", "بمقدم"])
    )
    has_cash_intent = any(w in text_clean for w in ["cash", "full payment", "one payment", "كاش", "نقدا", "نقداً", "كامل المبلغ"])

    if has_installment_intent:
        result["payment_preference"] = "installment"
        confidence["payment_preference"] = "explicit"
    elif has_cash_intent:
        result["payment_preference"] = "cash"
        confidence["payment_preference"] = "explicit"
    if result["payment_preference"] is None:
        ar_payment = _detect_arabic_payment_preference(text)
        if ar_payment:
            result["payment_preference"] = ar_payment
            confidence["payment_preference"] = "explicit"

    # monthly payment ceiling: "up to 50k a month" / "55,000 EGP/month" / "max monthly 40k" /
    # "max 25k monthly" (number-then-adjective form, not just number-then-"/month")
    monthly_match = re.search(
        r"(\d+(?:,\d{3})*(?:\.\d+)?\s*(?:k|thousand)?)\s*(?:egp)?\s*(?:/|a|per)\s*month", text
    ) or re.search(
        r"(\d+(?:,\d{3})*(?:\.\d+)?\s*(?:k|thousand)?)\s*(?:egp\s*)?monthly\b", text
    )
    if monthly_match:
        result["max_monthly_payment"] = _parse_money(monthly_match.group(1))
        confidence["max_monthly_payment"] = "explicit"
    else:
        ar_monthly = _parse_arabic_monthly_payment(text)
        if ar_monthly:
            result["max_monthly_payment"] = ar_monthly
            confidence["max_monthly_payment"] = "explicit"

    # down payment: "down payment of 300k" / "down payment up to 500,000" / "pay 500k down"
    # (number-before-"down" form, not just "down payment" followed by a number)
    down_match = re.search(
        r"down\s*payment[^\d]{0,15}(\d+(?:,\d{3})*(?:\.\d+)?\s*(?:m|million|k|thousand)?)", text
    ) or re.search(
        r"(\d+(?:,\d{3})*(?:\.\d+)?\s*(?:m|million|k|thousand)?)\s*down\b", text
    )
    if down_match:
        result["max_down_payment"] = _parse_money(down_match.group(1))
        confidence["max_down_payment"] = "explicit"
    else:
        ar_down = _parse_arabic_down_payment(text)
        if ar_down:
            result["max_down_payment"] = ar_down
            confidence["max_down_payment"] = "explicit"

    # budget range: "budget of 4.5 to 5.5 million" / "around 5 million" / "under 6M"
    range_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:m|million)?\s*(?:-|to|–)\s*(\d+(?:\.\d+)?)\s*(m|million)", text
    )
    if range_match:
        lo, hi, unit = range_match.groups()
        mult = 1_000_000 if unit in ("m", "million") else 1
        result["budget_min"] = float(lo) * mult
        result["budget_max"] = float(hi) * mult
        confidence["budget_min"] = confidence["budget_max"] = "explicit"
    else:
        under_match = re.search(r"(?:under|below|up to|max)\s+(?:\w+\s+)?(\d+(?:\.\d+)?)\s*(m|million)", text)
        around_match = re.search(
            r"(?:around|about|roughly|approximately|budget of|budget is)\s+(?:\w+\s+)?"
            r"(\d+(?:\.\d+)?)\s*(m|million)", text)
        if under_match:
            val = float(under_match.group(1)) * 1_000_000
            result["budget_max"] = val
            confidence["budget_max"] = "explicit"
            result["budget_min"] = val * 0.75
            confidence["budget_min"] = "inferred"  # derived estimate, not stated
        elif around_match:
            val = float(around_match.group(1)) * 1_000_000
            # neither bound was literally stated — only an approximate center point — so both
            # ends of the range are inferred, not explicit
            result["budget_min"] = val * 0.9
            result["budget_max"] = val * 1.1
            confidence["budget_min"] = confidence["budget_max"] = "inferred"
        else:
            # Arabic budget: real Egyptian phrasing states a single figure directly
            # ("الميزانية X مليون" / "ميزانيتي حوالي X مليون") rather than a range —
            # set budget_max to that figure with no min inferred, matching how this
            # is actually stated (not the English "around X" ±10% spread pattern,
            # since Arabic conversations in practice don't imply a range that way).
            ar_budget = _parse_arabic_money(text)
            if ar_budget:
                result["budget_max"] = ar_budget
                confidence["budget_max"] = "inferred" if "حوالي" in text or "تقريبا" in text else "explicit"

    # bedrooms: "3-bedroom", "3 bedrooms", "3BR"
    bed_match = re.search(r"(\d+)\s*(?:-|\s)?(?:bedroom|br\b|bed\b)", text)
    if bed_match:
        result["min_bedrooms"] = int(bed_match.group(1))
        confidence["min_bedrooms"] = "explicit"
    else:
        ar_beds = _detect_arabic_bedrooms(text)
        if ar_beds is not None:
            result["min_bedrooms"] = ar_beds
            confidence["min_bedrooms"] = "explicit"

    # locations
    found_locs = []
    for loc in KNOWN_LOCATIONS:
        if loc.lower() in text:
            found_locs.append("6th of October" if loc == "6 October" else loc)
    for alias, canonical in LOCATION_ALIASES.items():
        if alias in text and canonical not in found_locs:
            found_locs.append(canonical)
    for canonical in _detect_arabic_locations(text):
        if canonical not in found_locs:
            found_locs.append(canonical)
    result["preferred_locations"] = list(dict.fromkeys(found_locs))  # de-dupe, keep order
    if result["preferred_locations"]:
        confidence["preferred_locations"] = "explicit"

    # purpose
    if any(w in text for w in ["family", "kids", "children", "wife", "my parents"]):
        result["purpose"] = "family"
        confidence["purpose"] = "explicit"
    elif any(w in text for w in ["invest", "rental", "rent it out", "resale"]):
        result["purpose"] = "investment"
        confidence["purpose"] = "explicit"
    if result["purpose"] is None:
        ar_purpose = _detect_arabic_purpose(text)
        if ar_purpose:
            result["purpose"] = ar_purpose
            confidence["purpose"] = "explicit"

    # timeline
    if any(w in text for w in ["asap", "immediately", "this week", "urgent"]):
        result["purchase_timeline"] = "immediate"
        confidence["purchase_timeline"] = "explicit"
    elif any(w in text for w in ["1-3 month", "next month", "few months", "1 to 3 month"]):
        result["purchase_timeline"] = "1-3m"
        confidence["purchase_timeline"] = "explicit"
    elif any(w in text for w in ["3-6 month", "later this year"]):
        result["purchase_timeline"] = "3-6m"
        confidence["purchase_timeline"] = "explicit"
    elif any(w in text for w in ["not in a rush", "just looking", "someday", "next year"]):
        result["purchase_timeline"] = "6m+"
        confidence["purchase_timeline"] = "explicit"
    if result["purchase_timeline"] is None:
        ar_timeline = _detect_arabic_timeline(text)
        if ar_timeline:
            result["purchase_timeline"] = ar_timeline
            confidence["purchase_timeline"] = "explicit"

    # urgency — partly derived from timeline (inferred), partly from direct urgency language (explicit)
    urgency_explicit = any(w in text for w in ["asap", "urgent", "urgently", "right away", "immediately",
                                                 "this week", "not in a rush", "no rush", "just looking",
                                                 "just browsing", "someday", "no hurry"])
    result["urgency"] = _detect_urgency(text, result["purchase_timeline"])
    if result["urgency"] != "unknown":
        confidence["urgency"] = "explicit" if urgency_explicit else "inferred"

    # delivery preference
    result["delivery_preference"] = _detect_delivery_preference(text)
    if result["delivery_preference"]:
        confidence["delivery_preference"] = "explicit"

    # objections — heuristic pattern match only; genuinely best-effort, see module docstring
    result["objections"] = _detect_objections(text)
    if result["objections"]:
        confidence["objections"] = "inferred"  # pattern-matched, not a structured direct statement

    # Phase 3: negative preference detection + engagement signal
    result["removed_locations"] = _detect_removed_locations(text)
    if result["removed_locations"]:
        confidence["removed_locations"] = "inferred"
    result["engagement_signal"] = _detect_engagement_signal(text, result["objections"], result["removed_locations"])

    result["field_confidence"] = confidence
    return result


def extract_profile(message_text: str) -> dict:
    """Main entry point. Uses the LLM if a key is configured, else heuristics."""
    if ANTHROPIC_API_KEY:
        try:
            return extract_with_llm(message_text)
        except Exception as e:
            print(f"[extraction] LLM extraction failed ({e}), falling back to heuristics")
    return extract_with_heuristics(message_text)


def merge_profile_into_lead(lead_id: int, extracted: dict):
    """Merges newly-extracted fields into a lead's stored profile.
    Only overwrites a field if the new extraction found something (non-null/non-empty) —
    later messages refine the profile rather than erasing earlier context."""
    conn = get_connection()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        conn.close()
        raise ValueError(f"Lead {lead_id} not found")

    updates = {}
    simple_fields = ["budget_min", "budget_max", "payment_preference", "max_monthly_payment",
                      "max_down_payment", "min_bedrooms", "purpose", "purchase_timeline",
                      "urgency", "delivery_preference"]
    for f in simple_fields:
        val = extracted.get(f)
        if val is not None and val != "unknown":
            updates[f] = val

    locs = extracted.get("preferred_locations") or []
    removed_locs = set(extracted.get("removed_locations") or [])
    if locs or removed_locs:
        existing = set(l.strip() for l in (lead["preferred_locations"] or "").split(",") if l.strip())
        # Phase 3: a location explicitly ruled out this message is removed even if it was
        # already stored from an earlier message, and even if (contradictorily) it also
        # appears in this same message's `locs` — removal wins, since "I don't like Sheikh
        # Zayed" mentioning "Sheikh Zayed" should not re-add it via the positive-match path.
        merged = (existing.union(locs)) - removed_locs
        updates["preferred_locations"] = ",".join(sorted(merged))

    # objections accumulate across messages (a concern raised once doesn't un-raise itself),
    # same additive pattern as preferred_locations
    new_objections = extracted.get("objections") or []
    if new_objections:
        existing_objections = set(json.loads(lead["objections"])) if lead["objections"] else set()
        merged_objections = existing_objections.union(new_objections)
        updates["objections"] = json.dumps(sorted(merged_objections))

    # confidence is per-field and reflects the CURRENT known value's confidence, not cumulative —
    # so new confidence entries overwrite old ones for the same field, but fields not mentioned
    # in this message keep their previous confidence rating
    new_confidence = extracted.get("field_confidence") or {}
    if new_confidence:
        existing_confidence = json.loads(lead["extraction_confidence"]) if lead["extraction_confidence"] else {}
        existing_confidence.update(new_confidence)
        updates["extraction_confidence"] = json.dumps(existing_confidence)

    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE leads SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            (*updates.values(), lead_id),
        )
        conn.commit()
    conn.close()
    return updates


# Core fields a complete lead profile needs — used to compute what's still missing
# and to suggest the next question an agent should ask. Deliberately excludes fields
# like urgency/objections/delivery_preference, which are useful-if-present but not
# required to act on a lead the way budget/location/bedrooms/payment/timeline are.
CORE_FIELDS = ["budget_max", "payment_preference", "preferred_locations", "min_bedrooms",
               "purpose", "purchase_timeline"]

MISSING_FIELD_LABELS = {
    "budget_max": "budget", "payment_preference": "cash vs. installment preference",
    "preferred_locations": "preferred location(s)", "min_bedrooms": "number of bedrooms",
    "purpose": "purpose (family home vs. investment)", "purchase_timeline": "purchase timeline",
}

NEXT_QUESTION_SUGGESTIONS = {
    "budget_max": "What's your budget range for this?",
    "payment_preference": "Would you prefer to pay cash or on an installment plan?",
    "preferred_locations": "Which area(s) are you interested in?",
    "min_bedrooms": "How many bedrooms are you looking for?",
    "purpose": "Is this for your own family, or more of an investment?",
    "purchase_timeline": "What's your timeline — are you looking to move soon, or just exploring for now?",
}


def compute_missing_fields(lead_row) -> list:
    """lead_row: a sqlite3.Row (or dict) for a lead, as returned by SELECT * FROM leads.
    Returns the list of CORE_FIELDS that are still empty, in human-readable form."""
    missing = []
    for field in CORE_FIELDS:
        val = lead_row[field] if field in lead_row.keys() else None
        if val is None or val == "" or val == "unknown":
            missing.append(MISSING_FIELD_LABELS[field])
    return missing


def suggest_next_question(lead_row):
    """Returns a single suggested next question for the agent to ask, targeting the
    first still-missing core field — deliberately just one, not a checklist, since an
    agent should ask one thing at a time in a real conversation."""
    for field in CORE_FIELDS:
        val = lead_row[field] if field in lead_row.keys() else None
        if val is None or val == "" or val == "unknown":
            return NEXT_QUESTION_SUGGESTIONS[field]
    return None


if __name__ == "__main__":
    samples = [
        "Hi, I'm looking for a 3-bedroom apartment in New Cairo, budget around 5 million, "
        "prefer installments, up to 55,000 EGP a month, down payment max 600k. It's for my family.",
        "Looking for something in 6th of October, under 4M, I want to pay cash, need it ASAP.",
        "3 bedroom villa, North coast, investment, not in a rush",
    ]
    for s in samples:
        print(f"\nMESSAGE: {s}")
        print(json.dumps(extract_profile(s), indent=2))
