"""
Phase 2 evaluation: run the extraction engine against data/conversations_labeled.json
and report REAL precision/recall/F1 per field, broken down by language.

This is the honest counterpart to the roadmap's Section 17 — numbers are computed,
never invented. Run this and paste the actual printed output into any writeup;
do not hand-write plausible-looking F1 scores.

Usage:
    python3 eval_extraction.py                  # evaluate the heuristic extractor
    ANTHROPIC_API_KEY=sk-... python3 eval_extraction.py   # evaluate the LLM extractor instead
"""
import json
import os
from extraction import extract_profile

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "synthetic", "conversations_labeled.json")

FIELDS = ["budget_max", "payment_preference", "max_monthly_payment", "max_down_payment",
          "preferred_locations", "min_bedrooms", "purpose", "purchase_timeline"]

# Tolerance for numeric fields: extracted value counts as a match if within this
# fraction of the true value (extraction from natural language is rarely exact
# to the EGP, and shouldn't be scored as if it should be).
NUMERIC_TOLERANCE = 0.15


def values_match(field, predicted, true):
    if true is None and predicted is None:
        return None  # not applicable — excluded from precision/recall for this field on this example
    if true is None or predicted is None:
        return False

    if field == "preferred_locations":
        true_set = set(true) if isinstance(true, list) else {true}
        pred_set = set(predicted) if isinstance(predicted, list) else {predicted}
        return len(true_set & pred_set) > 0

    if field in ("budget_max", "max_monthly_payment", "max_down_payment"):
        if true == 0:
            return predicted == 0
        return abs(predicted - true) / true <= NUMERIC_TOLERANCE

    return predicted == true


def evaluate():
    with open(DATA_PATH, encoding="utf-8") as f:
        dataset = json.load(f)

    # per-field, per-language confusion counts
    results = {field: {"tp": 0, "fp": 0, "fn": 0, "tn": 0} for field in FIELDS}
    by_language = {}

    for convo in dataset:
        full_text = " ".join(convo["messages"])
        # Concatenating all messages and extracting once mirrors how merge_profile_into_lead
        # would behave after all messages arrive — a reasonable evaluation simplification
        # since the heuristic extractor doesn't maintain state across calls itself.
        predicted = extract_profile(full_text)
        true = convo["labels"]
        lang = convo["language"]
        by_language.setdefault(lang, {field: {"tp": 0, "fp": 0, "fn": 0, "tn": 0} for field in FIELDS})

        for field in FIELDS:
            pred_val = predicted.get(field)
            true_val = true.get(field)
            match = values_match(field, pred_val, true_val)

            for bucket in (results[field], by_language[lang][field]):
                if match is None:
                    if pred_val in (None, [], ""):
                        bucket["tn"] += 1
                    else:
                        bucket["fp"] += 1  # predicted something where truth says nothing
                elif match:
                    bucket["tp"] += 1
                else:
                    if pred_val in (None, [], ""):
                        bucket["fn"] += 1
                    else:
                        bucket["fp"] += 1

    return results, by_language, len(dataset)


def precision_recall_f1(counts):
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    precision = tp / (tp + fp) if (tp + fp) > 0 else float("nan")
    recall = tp / (tp + fn) if (tp + fn) > 0 else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else float("nan")
    return precision, recall, f1


def print_report(results, by_language, n):
    backend = "LLM (Anthropic API)" if os.environ.get("ANTHROPIC_API_KEY") else "heuristic (regex/keyword)"
    print(f"Evaluated {n} labeled conversations. Extraction backend: {backend}\n")

    print(f"{'Field':24s} {'Precision':>10s} {'Recall':>10s} {'F1':>10s}   {'TP/FP/FN/TN'}")
    print("-" * 78)
    for field in FIELDS:
        c = results[field]
        p, r, f1 = precision_recall_f1(c)
        p_str = f"{p:.2f}" if p == p else "  n/a"
        r_str = f"{r:.2f}" if r == r else "  n/a"
        f1_str = f"{f1:.2f}" if f1 == f1 else "  n/a"
        print(f"{field:24s} {p_str:>10s} {r_str:>10s} {f1_str:>10s}   {c['tp']}/{c['fp']}/{c['fn']}/{c['tn']}")

    print("\nBy language (F1 only):")
    print(f"{'Field':24s}", *[f"{lang:>10s}" for lang in by_language])
    for field in FIELDS:
        row = [field.ljust(24)]
        for lang in by_language:
            _, _, f1 = precision_recall_f1(by_language[lang][field])
            row.append(f"{f1:.2f}" if f1 == f1 else "n/a".rjust(4))
        print(" ".join(str(x).rjust(10) if i > 0 else x for i, x in enumerate(row)))


if __name__ == "__main__":
    results, by_language, n = evaluate()
    print_report(results, by_language, n)
