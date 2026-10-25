"""
Generates the Phase 2 evaluation dataset: realistic Egyptian WhatsApp-style
lead conversations with ground-truth structured labels, in the language mix
real Egyptian brokerages actually see (Arabic / English / Arabizi / mixed).

Honesty note (read this before citing this as "real data" anywhere): this is
TEMPLATED synthetic data, not scraped or copied from real conversations —
which is exactly what you want for a dataset that gets published on GitHub,
since real WhatsApp exports should never be published even anonymized
without explicit consent. Each language has several hand-authored phrasing
variants per requirement slot (budget, location, bedrooms, payment
preference, timeline, purpose), combined to produce realistic multi-turn
conversations with genuine linguistic variety — not just number substitution
in one fixed sentence. Volume comes from combining slots, not from
duplicating text.

Output: data/conversations_labeled.json
Run:
    python3 generate_conversations.py
Then evaluate the extractor against it:
    python3 eval_extraction.py
"""
import json
import os
import random

random.seed(11)
OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "synthetic", "conversations_labeled.json")

LOCATIONS = ["New Cairo", "6th of October", "Sheikh Zayed", "North Coast"]
BEDROOM_OPTIONS = [2, 3, 4]
PURPOSES = ["family", "investment", None]
TIMELINES = ["immediate", "1-3m", "3-6m", "6m+", None]

# ---------------------------------------------------------------------------
# Per-language phrasing banks. Each function takes the sampled slot values and
# returns a list of message strings (a realistic multi-turn exchange).
# ---------------------------------------------------------------------------

LOC_AR = {"New Cairo": "التجمع الخامس", "6th of October": "أكتوبر", "Sheikh Zayed": "الشيخ زايد", "North Coast": "الساحل الشمالي"}
LOC_ARZ = {"New Cairo": "new cairo", "6th of October": "october", "Sheikh Zayed": "sheikh zayed", "North Coast": "north coast"}

TIMELINE_AR = {"immediate": "محتاجها بسرعة، خلال الأسبوعين الجايين", "1-3m": "حابب أقفلها خلال شهر لشهرين",
               "3-6m": "مش مستعجل، عندي كام شهر", "6m+": "لسه بدور، مش هاخد قرار قريب", None: None}
TIMELINE_EN = {"immediate": "Need this ASAP, ideally within a couple weeks", "1-3m": "Looking to close within 1-3 months",
               "3-6m": "No major rush, maybe in the next 3-6 months", "6m+": "Just exploring for now, no timeline yet", None: None}
TIMELINE_ARZ = {"immediate": "3ayzha bsor3a, within 2 weeks", "1-3m": "hab2fel deal within shahr aw shahrein",
                "3-6m": "mesh mesta3gel, 3andy kam shahr", "6m+": "lesa bdawar, mesh mo2arrar hanas 2arrib", None: None}

PURPOSE_AR = {"family": "دي لسكني أنا وعيلتي", "investment": "دي استثمار مش سكن", None: None}
PURPOSE_EN = {"family": "This is for my family to live in", "investment": "This is purely an investment, not for living", None: None}
PURPOSE_ARZ = {"family": "da le sokna, ana w 3ela ty", "investment": "da estithmar mesh sokna", None: None}


def fmt_money_ar(n):
    m = n / 1_000_000
    return f"{m:g} مليون" if m == int(m) else f"{m:.1f} مليون"


def fmt_money_en(n):
    m = n / 1_000_000
    return f"{m:g}M" if m == int(m) else f"{m:.1f}M"


def gen_arabic(slots):
    loc = LOC_AR[slots["location"]]
    msgs = [random.choice([
        f"السلام عليكم، عايز اعرف عندكوا شقق في {loc}؟",
        f"ازيك، عندكم عقارات في {loc}؟",
        f"محتاج {'فيلا' if slots['bedrooms'] >= 4 else 'شقة'} في {loc}",
    ])]
    msgs.append(random.choice([
        f"{slots['bedrooms']} غرف، وميزانيتي حوالي {fmt_money_ar(slots['budget'])}",
        f"عايزها {slots['bedrooms']} غرف تقريبا، الميزانية {fmt_money_ar(slots['budget'])}",
    ]))
    if slots["payment_preference"] == "installment":
        msgs.append(random.choice([
            f"بس عايزها بالتقسيط، مقدم {int(slots['down_payment']/1000)} الف وقسط شهري لغاية {int(slots['monthly']/1000)} الف",
            f"حابب أقساط، مقدم حوالي {int(slots['down_payment']/1000)} الف والقسط الشهري ميزيدش عن {int(slots['monthly']/1000)} الف",
        ]))
    else:
        msgs.append(random.choice(["هدفعها كاش", "كاش أحسن بالنسبالي"]))
    if PURPOSE_AR[slots["purpose"]]:
        msgs.append(PURPOSE_AR[slots["purpose"]])
    if TIMELINE_AR[slots["timeline"]]:
        msgs.append(TIMELINE_AR[slots["timeline"]])
    return msgs


def gen_english(slots):
    loc = slots["location"]
    msgs = [random.choice([
        f"Hi, do you have anything in {loc}?",
        f"Hello, I saw your ad for properties in {loc}",
        f"Looking for a place in {loc}",
    ])]
    msgs.append(random.choice([
        f"{slots['bedrooms']} bedrooms, budget around {fmt_money_en(slots['budget'])} EGP",
        f"Need at least {slots['bedrooms']} bedrooms, my budget is roughly {fmt_money_en(slots['budget'])}",
    ]))
    if slots["payment_preference"] == "installment":
        msgs.append(random.choice([
            f"I'd prefer installments — down payment up to {int(slots['down_payment']):,}, "
            f"max monthly around {int(slots['monthly']):,} EGP",
            f"Installments please, max {int(slots['monthly']):,} a month, down payment {int(slots['down_payment']):,}",
        ]))
    else:
        msgs.append(random.choice(["I'll be paying cash", "Cash buyer, no installments needed"]))
    if PURPOSE_EN[slots["purpose"]]:
        msgs.append(PURPOSE_EN[slots["purpose"]])
    if TIMELINE_EN[slots["timeline"]]:
        msgs.append(TIMELINE_EN[slots["timeline"]])
    return msgs


def gen_arabizi(slots):
    loc = LOC_ARZ[slots["location"]]
    msgs = [random.choice([
        f"hi, 3ayz shakka fe {loc}",
        f"ezayak, 3andokom units fe {loc}?",
        f"mehtag {'villa' if slots['bedrooms'] >= 4 else 'shakka'} fe {loc}",
    ])]
    msgs.append(random.choice([
        f"{slots['bedrooms']} rooms, budget {fmt_money_en(slots['budget'])} taqriban",
        f"3ayzha {slots['bedrooms']} oda, el budget hwaly {fmt_money_en(slots['budget'])}",
    ]))
    if slots["payment_preference"] == "installment":
        msgs.append(random.choice([
            f"3ayz a2sat, el mo2adam {int(slots['down_payment']/1000)} alf, max {int(slots['monthly']/1000)} alf fel shahr",
            f"hab2a 3ala a2sat, down payment {int(slots['down_payment']/1000)}k, monthly max {int(slots['monthly']/1000)}k",
        ]))
    else:
        msgs.append(random.choice(["hadfa3 cash", "cash mesh a2sat"]))
    if PURPOSE_ARZ[slots["purpose"]]:
        msgs.append(PURPOSE_ARZ[slots["purpose"]])
    if TIMELINE_ARZ[slots["timeline"]]:
        msgs.append(TIMELINE_ARZ[slots["timeline"]])
    return msgs


def gen_mixed(slots):
    loc = slots["location"]
    msgs = [random.choice([
        f"عايز apartment في {loc}",
        f"Hi عايز {'villa' if slots['bedrooms'] >= 4 else 'شقة'} في {loc}",
    ])]
    msgs.append(random.choice([
        f"{slots['bedrooms']} bedrooms, وميزانيتي around {fmt_money_en(slots['budget'])}",
        f"عايزها {slots['bedrooms']} غرف, budget حوالي {fmt_money_en(slots['budget'])}",
    ]))
    if slots["payment_preference"] == "installment":
        msgs.append(random.choice([
            f"عايز installment, max monthly {int(slots['monthly']):,} EGP",
            f"حابب أقساط، down payment حوالي {int(slots['down_payment']):,}",
        ]))
    else:
        msgs.append(random.choice(["هدفع cash", "cash budget"]))
    if slots["purpose"] == "family":
        msgs.append(random.choice(["غرضي family residence", "for my family, مش استثمار"]))
    elif slots["purpose"] == "investment":
        msgs.append(random.choice(["ده استثمار, not for living", "investment purpose بس"]))
    if slots["timeline"]:
        msgs.append(random.choice(["مش هستعجل، just checking options", "need it soon, urgent شوية"]))
    return msgs


GENERATORS = {"arabic": gen_arabic, "english": gen_english, "arabizi": gen_arabizi, "mixed": gen_mixed}
TARGET_COUNTS = {"arabic": 40, "english": 20, "arabizi": 20, "mixed": 20}  # 40/20/20/20 distribution, 100 total


def sample_slots():
    location = random.choice(LOCATIONS)
    bedrooms = random.choice(BEDROOM_OPTIONS)
    budget = random.choice(list(range(2_500_000, 12_000_001, 500_000)))
    payment_preference = random.choice(["cash", "installment", "installment"])  # installment more common
    down_payment = round(budget * random.uniform(0.08, 0.18), -3) if payment_preference == "installment" else None
    monthly = round((budget * random.uniform(1.05, 1.15) - (down_payment or 0)) / random.choice([60, 72, 84, 96]), -2) \
        if payment_preference == "installment" else None
    purpose = random.choice(PURPOSES)
    timeline = random.choice(TIMELINES)
    return {"location": location, "bedrooms": bedrooms, "budget": budget,
            "payment_preference": payment_preference, "down_payment": down_payment, "monthly": monthly,
            "purpose": purpose, "timeline": timeline}


def slots_to_labels(slots):
    return {
        "budget_min": None, "budget_max": slots["budget"],
        "payment_preference": slots["payment_preference"],
        "max_monthly_payment": slots["monthly"], "max_down_payment": slots["down_payment"],
        "preferred_locations": [slots["location"]], "min_bedrooms": slots["bedrooms"],
        "purpose": slots["purpose"], "purchase_timeline": slots["timeline"],
    }


def build_dataset():
    dataset = []
    convo_id = 1
    for lang, count in TARGET_COUNTS.items():
        gen_fn = GENERATORS[lang]
        for _ in range(count):
            slots = sample_slots()
            messages = gen_fn(slots)
            dataset.append({
                "conversation_id": f"C{convo_id:04d}", "language": lang,
                "messages": messages, "labels": slots_to_labels(slots),
            })
            convo_id += 1
    random.shuffle(dataset)
    return dataset


if __name__ == "__main__":
    dataset = build_dataset()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    counts = {}
    for c in dataset:
        counts[c["language"]] = counts.get(c["language"], 0) + 1
    print(f"Wrote {len(dataset)} labeled conversations to {OUT_PATH}")
    print("Language distribution:", counts)
    print("\nSample:")
    print(json.dumps(dataset[0], ensure_ascii=False, indent=2))
