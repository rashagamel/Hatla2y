"""
Installment term extraction from listing descriptions (Arabic & English).

Extracts genuine payment plan terms that developers and brokers include in
listing descriptions on Egyptian property portals (PropertyFinder, Aqarmap):
  - Plan duration (years / months)
  - Down payment percentage (e.g. 5%, 10%, 15%) or fixed amount
  - Monthly payment amount (when quoted)
  - Handover / delivery year

When extracted, these terms allow the system to store genuine, listing-sourced
installment plans instead of relying solely on the fallback assumed 7-year term.
Every plan records its provenance (extracted_from_description vs assumed_default).
"""
import re
import json

ARABIC_DIGITS_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

ARABIC_YEAR_WORDS = {
    "سنتين": 2,
    "ثلاث سنوات": 3, "ثلاث سنين": 3, "3 سنوات": 3,
    "اربع سنوات": 4, "أربع سنوات": 4, "اربع سنين": 4, "أربع سنين": 4,
    "خمس سنوات": 5, "خمس سنين": 5,
    "ست سنوات": 6, "ست سنين": 6,
    "سبع سنوات": 7, "سبع سنين": 7,
    "ثماني سنوات": 8, "ثمان سنوات": 8, "ثماني سنين": 8, "ثمان سنين": 8,
    "تسع سنوات": 9, "تسع سنين": 9,
    "عشر سنوات": 10, "عشر سنين": 10,
    "اثنا عشر سنة": 12, "12 سنة": 12,
}


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.translate(ARABIC_DIGITS_MAP)
    return text


def extract_plan_duration_years(text: str) -> tuple[int | None, str | None]:
    """
    Extracts installment plan duration in years from text.
    Returns (years, matched_snippet).
    """
    if not text:
        return None, None
    norm = normalize_text(text)

    # 1. Arabic word numerals first
    for word_phrase, yrs in ARABIC_YEAR_WORDS.items():
        if word_phrase in norm:
            return yrs, word_phrase

    # 2. Arabic digits with years/months
    # e.g. "أقساط على 8 سنوات", "تقسيط حتى 10 سنين", "على 7 سنوات", "8 سنوات تقسيط"
    ar_patterns = [
        r"(?:أقساط|اقساط|تقسيط|قسط)\s*(?:على|حتى|لمدة)?\s*(\d{1,2})\s*(?:سنوات|سنين|سنة)",
        r"(?:على|خلال|حتى|لمدة)\s*(\d{1,2})\s*(?:سنوات|سنين|سنة)\s*(?:أقساط|اقساط|تقسيط|بدون فوائد)?",
        r"(\d{1,2})\s*(?:سنوات|سنين|سنة)\s*(?:أقساط|اقساط|تقسيط|تسهيلات)",
    ]
    for pat in ar_patterns:
        m = re.search(pat, norm, flags=re.IGNORECASE)
        if m:
            val = int(m.group(1))
            if 1 <= val <= 25:
                return val, m.group(0)

    # Arabic months: e.g. "على 84 شهر", "تقسيط على 96 شهر", "60 شهر"
    ar_month_pat = r"(?:أقساط|اقساط|تقسيط|على|خلال)?\s*(\d{2,3})\s*(?:شهر|شهور)"
    m_ar_mo = re.search(ar_month_pat, norm, flags=re.IGNORECASE)
    if m_ar_mo:
        months = int(m_ar_mo.group(1))
        if 12 <= months <= 300:
            years = round(months / 12)
            if years >= 1:
                return years, m_ar_mo.group(0)

    # 3. English patterns
    en_patterns = [
        r"(\d{1,2})\s*(?:years?|yrs?)\s*(?:equal\s*)?installments?",
        r"installments?\s*(?:over|up\s*to)?\s*(\d{1,2})\s*(?:years?|yrs?)",
        r"payment\s*plan\s*(?:over|up\s*to)?\s*(\d{1,2})\s*(?:years?|yrs?)",
        r"over\s*(\d{1,2})\s*(?:years?|yrs?)\s*(?:installments?|payment\s*plan)?",
        r"(\d{1,2})\s*(?:years?|yrs?)\s*payment\s*plan",
    ]
    for pat in en_patterns:
        m = re.search(pat, norm, flags=re.IGNORECASE)
        if m:
            val = int(m.group(1))
            if 1 <= val <= 25:
                return val, m.group(0)

    # English months: e.g. "over 84 months", "84 equal monthly installments"
    en_month_pat = r"(\d{2,3})\s*(?:equal\s*)?(?:months?|monthly\s*installments?)"
    m_en_mo = re.search(en_month_pat, norm, flags=re.IGNORECASE)
    if m_en_mo:
        months = int(m_en_mo.group(1))
        if 12 <= months <= 300:
            years = round(months / 12)
            if years >= 1:
                return years, m_en_mo.group(0)

    return None, None


def extract_down_payment(text: str, total_price: float = None) -> tuple[float | None, float | None, str | None]:
    """
    Extracts down payment from text.
    Returns (dp_pct, dp_amount, matched_snippet).
    """
    if not text:
        return None, None, None
    norm = normalize_text(text)

    # 1. Percentage patterns
    pct_patterns = [
        # Arabic: مقدم 10%, بمقدم 5%, دفعة مقدمة 15%, 10% مقدم
        r"(?:مقدم|بمقدم|دفعة\s*مقدمة|ادفع|دفع)\s*(\d+(?:\.\d+)?)\s*%",
        r"(\d+(?:\.\d+)?)\s*%\s*(?:مقدم|دفعة\s*مقدمة|down\s*payment|dp)",
        # English: 10% down payment, 5% DP, down payment 10%
        r"(\d+(?:\.\d+)?)\s*%\s*(?:down\s*payment|dp|down)",
        r"(?:down\s*payment|dp|down)\s*(?:of\s*)?(\d+(?:\.\d+)?)\s*%",
    ]
    for pat in pct_patterns:
        m = re.search(pat, norm, flags=re.IGNORECASE)
        if m:
            pct = float(m.group(1))
            if 0 < pct <= 70:
                amount = round(total_price * (pct / 100.0), -2) if total_price else None
                return pct, amount, m.group(0)

    # 2. Fixed amount patterns
    amt_patterns = [
        r"(?:مقدم|بمقدم|دفعة\s*مقدمة)\s*([0-9\.,]+)\s*(الف|ألف|مليون|k|m|million|thousand|egp|جنيه)?",
        r"(?:down\s*payment|dp)[:\s]*([0-9\.,]+)\s*(k|m|million|thousand|egp)?",
    ]
    for pat in amt_patterns:
        m = re.search(pat, norm, flags=re.IGNORECASE)
        if m:
            raw_num = m.group(1).replace(",", "")
            try:
                num = float(raw_num)
            except ValueError:
                continue
            unit = (m.group(2) or "").lower() if len(m.groups()) >= 2 else ""
            if unit in ("الف", "ألف", "k", "thousand"):
                num *= 1_000
            elif unit in ("مليون", "m", "million"):
                num *= 1_000_000
            elif num < 500:  # e.g. "مقدم 500" -> 500k
                num *= 1_000

            if 10_000 <= num <= 50_000_000:
                pct = round((num / total_price) * 100.0, 1) if total_price and total_price > 0 else None
                return pct, num, m.group(0)

    return None, None, None


def extract_monthly_payment(text: str) -> tuple[float | None, str | None]:
    """
    Extracts monthly payment if explicitly mentioned in text.
    Returns (monthly_amount, matched_snippet).
    """
    if not text:
        return None, None
    norm = normalize_text(text)

    patterns = [
        r"(?:قسط\s*شهري|شهريا|شهري)\s*([0-9\.,]+)\s*(الف|ألف|k|egp|جنيه)?",
        r"(?:monthly|per\s*month|monthly\s*installment)[:\s]*([0-9\.,]+)\s*(k|m|thousand|egp)?",
    ]
    for pat in patterns:
        m = re.search(pat, norm, flags=re.IGNORECASE)
        if m:
            raw_num = m.group(1).replace(",", "")
            try:
                num = float(raw_num)
            except ValueError:
                continue
            unit = (m.group(2) or "").lower() if len(m.groups()) >= 2 else ""
            if unit in ("الف", "ألف", "k", "thousand"):
                num *= 1_000
            if 1_000 <= num <= 2_000_000:
                return num, m.group(0)

    return None, None


def extract_delivery_year(text: str) -> tuple[int | None, str | None]:
    """Extracts delivery/handover year if mentioned (e.g. 2026, 2027)."""
    if not text:
        return None, None
    norm = normalize_text(text)
    pat = r"(?:delivery|handover|تسليم|استلام)\s*(?:in|عام|سنة)?\s*(202[4-9]|203[0-5])"
    m = re.search(pat, norm, flags=re.IGNORECASE)
    if m:
        return int(m.group(1)), m.group(0)

    pat_rev = r"(202[4-9]|203[0-5])\s*(?:delivery|handover|تسليم|استلام)"
    m_rev = re.search(pat_rev, norm, flags=re.IGNORECASE)
    if m_rev:
        return int(m_rev.group(1)), m_rev.group(0)

    return None, None


def extract_installment_terms(description: str, total_price: float = None) -> dict:
    """
    Comprehensive extraction of all payment plan terms from listing description.
    """
    if not description:
        return {
            "has_installment_mention": False,
            "years": None,
            "down_payment_pct": None,
            "down_payment_amount": None,
            "monthly_payment": None,
            "delivery_year": None,
            "matched_snippets": {},
        }

    years, y_snip = extract_plan_duration_years(description)
    dp_pct, dp_amt, dp_snip = extract_down_payment(description, total_price=total_price)
    monthly, m_snip = extract_monthly_payment(description)
    delivery, d_snip = extract_delivery_year(description)

    snippets = {}
    if y_snip:
        snippets["duration"] = y_snip
    if dp_snip:
        snippets["down_payment"] = dp_snip
    if m_snip:
        snippets["monthly_payment"] = m_snip
    if d_snip:
        snippets["delivery_year"] = d_snip

    has_terms = bool(years or dp_pct or monthly or dp_amt)

    return {
        "has_installment_mention": has_terms,
        "years": years,
        "down_payment_pct": dp_pct,
        "down_payment_amount": dp_amt,
        "monthly_payment": monthly,
        "delivery_year": delivery,
        "matched_snippets": snippets,
    }


def resolve_installment_plan(description: str, price: float,
                             structured_down_payment: float = None,
                             fallback_years: int = 7,
                             default_dp_pct: float = 10.0) -> dict | None:
    """
    Builds a complete, validated installment plan by combining structured data
    and extracted description terms with full provenance tracking.

    Returns dict with:
      - years: int
      - down_payment_pct: float
      - monthly_payment: float
      - total_price: float
      - plan_source: 'extracted_from_description' | 'structured_listing' | 'assumed_default'
      - extracted_terms: JSON string
    Or None if no installment plan applies.
    """
    if not price or price <= 0:
        return None

    terms = extract_installment_terms(description, total_price=price)

    # Duration: prefer extracted years, then fallback
    years = terms["years"]
    is_years_extracted = years is not None
    if not years:
        years = fallback_years

    # Down payment: prefer structured down payment if valid, else extracted dp
    dp_pct = None
    dp_amt = None
    is_dp_extracted = False

    if structured_down_payment and 0 < structured_down_payment < price:
        dp_amt = structured_down_payment
        dp_pct = round((dp_amt / price) * 100.0, 1)
    elif terms["down_payment_pct"] is not None:
        dp_pct = terms["down_payment_pct"]
        dp_amt = terms["down_payment_amount"] or round(price * (dp_pct / 100.0), -2)
        is_dp_extracted = True
    elif terms["down_payment_amount"] is not None and 0 < terms["down_payment_amount"] < price:
        dp_amt = terms["down_payment_amount"]
        dp_pct = round((dp_amt / price) * 100.0, 1)
        is_dp_extracted = True
    else:
        # Default down payment pct assumption
        dp_pct = default_dp_pct
        dp_amt = round(price * (dp_pct / 100.0), -2)

    # Monthly payment: prefer extracted monthly payment if compatible, else compute
    monthly = terms["monthly_payment"]
    if not monthly:
        monthly = round((price - dp_amt) / (years * 12), -2)

    # Determine provenance category honestly
    if is_years_extracted and is_dp_extracted:
        plan_source = "extracted_from_description"
    elif is_years_extracted:
        plan_source = "extracted_duration"
    elif is_dp_extracted:
        plan_source = "extracted_down_payment"
    elif structured_down_payment:
        plan_source = "structured_listing"
    else:
        plan_source = "assumed_default"

    return {
        "years": int(years),
        "down_payment_pct": float(dp_pct),
        "monthly_payment": float(monthly),
        "total_price": float(price),
        "plan_source": plan_source,
        "extracted_terms": json.dumps(terms, ensure_ascii=False),
    }
