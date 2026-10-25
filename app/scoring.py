"""
Phase 3 — Lead scoring.

Honest disclaimer (say this in interviews too): there is no real historical
outcome data here — no brokerage dataset exists for this project. So this
trains a GradientBoostingClassifier (scikit-learn; xgboost isn't installable
in this sandbox — swap in `xgboost.XGBClassifier` when you have real pip
access, the interface is nearly identical) on SYNTHETIC data generated from
a hand-specified set of rules that encode reasonable domain assumptions
(faster response = more likely to convert, budget-inventory mismatch hurts,
etc.), plus noise. The model then learns to recover and generalize those
rules from features alone — which is exactly what you'd want it to do once
you swap in real logged outcomes from `outcomes` table down the line.

Features used (all things you can compute from `leads` + `lead_events` +
`matches` without needing labeled data yourself):
    days_since_created
    hours_to_first_response      (proxy: time between first two events)
    num_events                   (engagement / activity level)
    num_property_views
    num_viewing_requests
    best_match_score             (from matching.py — inventory fit)
    budget_defined                (bool: did they give a real budget)
    payment_preference_known
    timeline_urgency_score        (immediate=1.0 ... 6m+=0.2, unknown=0.1)
    channel_whatsapp              (bool)

No SHAP (not installable here either) — per-lead explanations are computed
as a lightweight approximation: for each feature, we compare the lead's
(scaled) value to the training set mean, and weight the deviation by the
model's global feature_importances_. This is NOT a substitute for real SHAP
values, but works for consistent, greenlight/redflag styled explanations at
inference time without heavier dependencies. Swap in `shap.TreeExplainer`
for production.
"""
import json
import pickle
import random
import os
import threading
import logging
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from db import get_connection

logger = logging.getLogger(__name__)

MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "processed", "lead_scoring_model.pkl")
MODEL_VERSION = "v1-synthetic-gbc"

FEATURE_NAMES = [
    "days_since_created", "hours_to_first_response", "num_events",
    "num_property_views", "num_viewing_requests", "best_match_score",
    "budget_defined", "payment_preference_known", "timeline_urgency_score",
    "channel_whatsapp",
]

TIMELINE_URGENCY = {"immediate": 1.0, "1-3m": 0.7, "3-6m": 0.4, "6m+": 0.2, "unknown": 0.1, None: 0.1}


# ---------------------------------------------------------------------------
# Synthetic training data
# ---------------------------------------------------------------------------
def generate_synthetic_dataset(n=2000, seed=7):
    rng = random.Random(seed)
    X, y = [], []
    for _ in range(n):
        days_since_created = rng.uniform(0, 60)
        hours_to_first_response = rng.expovariate(1 / 24)  # avg 24h, long tail
        num_events = rng.randint(1, 25)
        num_property_views = rng.randint(0, min(num_events, 15))
        num_viewing_requests = rng.randint(0, min(2, num_property_views))
        best_match_score = rng.uniform(30, 100)
        budget_defined = rng.random() < 0.75
        payment_preference_known = rng.random() < 0.7
        timeline_urgency_score = rng.choice(list(TIMELINE_URGENCY.values()))
        channel_whatsapp = rng.random() < 0.8

        # Ground-truth rule (+ noise) the model has to learn to approximate:
        logit = (
            -2.0
            + 0.06 * num_property_views
            + 0.9 * num_viewing_requests
            + 0.015 * best_match_score
            + 0.9 * budget_defined
            + 0.5 * payment_preference_known
            + 1.6 * timeline_urgency_score
            + 0.3 * channel_whatsapp
            - 0.02 * hours_to_first_response
            - 0.01 * days_since_created
            + rng.gauss(0, 0.7)  # noise
        )
        prob = 1 / (1 + np.exp(-logit))
        label = 1 if rng.random() < prob else 0

        X.append([days_since_created, hours_to_first_response, num_events, num_property_views,
                   num_viewing_requests, best_match_score, float(budget_defined),
                   float(payment_preference_known), timeline_urgency_score, float(channel_whatsapp)])
        y.append(label)
    return np.array(X), np.array(y)


def train_model(save: bool = True):
    """
    Trains the default heuristic prior model on synthetic data.
    Explicitly labeled as 'heuristic_prior' — does NOT claim empirical conversion probability.
    """
    X, y = generate_synthetic_dataset()
    split = int(len(X) * 0.85)
    X_train, X_test, y_train, y_test = X[:split], X[split:], y[:split], y[split:]

    model = GradientBoostingClassifier(n_estimators=150, max_depth=3, learning_rate=0.08, random_state=7)
    model.fit(X_train, y_train)
    train_acc = model.score(X_train, y_train)
    test_acc = model.score(X_test, y_test)

    correlations = np.array([
        np.corrcoef(X_train[:, i], y_train)[0, 1] if X_train[:, i].std() > 0 else 0.0
        for i in range(X_train.shape[1])
    ])
    correlations = np.nan_to_num(correlations)

    bundle = {
        "model": model,
        "feature_means": X_train.mean(axis=0),
        "feature_stds": X_train.std(axis=0) + 1e-6,
        "feature_correlations": correlations,
        "model_version": MODEL_VERSION,
        "model_type": "heuristic_prior",
        "empirical_probability_available": False,
        "sample_size": len(X_train),
    }

    if save:
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(bundle, f)

    print(f"Trained heuristic prior on {len(X_train)} synthetic examples, tested on {len(X_test)}.")
    print(f"Train accuracy: {train_acc:.3f} | Test accuracy: {test_acc:.3f}")
    print("Feature importances:")
    for name, imp in sorted(zip(FEATURE_NAMES, model.feature_importances_), key=lambda t: -t[1]):
        print(f"  {name:28s} {imp:.3f}")
    return model


def train_empirical_model(conn=None, min_outcomes: int = 25, save: bool = True):
    """
    Trains a genuine empirical ML conversion model strictly on REAL historical outcomes
    (no synthetic rows), once sufficient real outcomes have been recorded.
    """
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()

    rows = conn.execute("""
        SELECT lead_id, outcome FROM outcomes
        WHERE outcome IN ('won','lost','viewing_booked','negotiating','no_response')
        ORDER BY created_at DESC
    """).fetchall()
    if owns_conn:
        conn.close()

    outcome_labels = {"won": 1, "viewing_booked": 1, "negotiating": 1, "lost": 0, "no_response": 0}
    seen_leads = set()
    X, y = [], []
    for r in rows:
        if r["lead_id"] in seen_leads:
            continue
        seen_leads.add(r["lead_id"])
        try:
            feats, _ = compute_features(r["lead_id"], live_fit=False)  # avoid a full inventory pass per lead
            X.append(feats)
            y.append(outcome_labels[r["outcome"]])
        except Exception:
            continue

    if len(X) < min_outcomes:
        return None

    X = np.array(X)
    y = np.array(y)

    model = GradientBoostingClassifier(n_estimators=100, max_depth=2, learning_rate=0.05, random_state=42)
    model.fit(X, y)

    correlations = np.array([
        np.corrcoef(X[:, i], y)[0, 1] if X[:, i].std() > 0 else 0.0
        for i in range(X.shape[1])
    ])
    correlations = np.nan_to_num(correlations)

    bundle = {
        "model": model,
        "feature_means": X.mean(axis=0),
        "feature_stds": X.std(axis=0) + 1e-6,
        "feature_correlations": correlations,
        "model_version": f"v2-empirical-real-outcomes-N{len(X)}",
        "model_type": "empirical_real_outcomes",
        "empirical_probability_available": True,
        "sample_size": len(X),
    }

    if save:
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(bundle, f)

    print(f"[OK] Trained empirical conversion model on {len(X)} real outcomes (zero synthetic rows).")
    return model


def _load_model():
    if not os.path.exists(MODEL_PATH):
        train_model()
    try:
        with open(MODEL_PATH, "rb") as f:
            return pickle.load(f)
    except Exception:
        # Retrain cleanly if unpickling fails (e.g. numpy binary compatibility)
        train_model()
        with open(MODEL_PATH, "rb") as f:
            return pickle.load(f)


# ---------------------------------------------------------------------------
# Live inventory-fit summary
#
# One place answers "does any available unit actually fit this lead?", computed
# by running the matching engine (not by counting rows in the `matches` log
# table, which only records what was shown and used to accumulate on every page
# view). Cached per lead + lead requirements + inventory stamp, so the several
# endpoints the dashboard fires in parallel share one pass over the inventory.
# ---------------------------------------------------------------------------
NO_FIT_SCORE_CAP = 45.0   # keep equal to matching.HARD_FAIL_CAP * 100

_FIT_CACHE = {}
_FIT_CACHE_MAX = 64
_FIT_LOCK = threading.RLock()
_LEAD_FIT_FIELDS = ("budget_min", "budget_max", "payment_preference", "max_monthly_payment",
                    "max_down_payment", "preferred_locations", "min_bedrooms")


def clear_fit_cache():
    with _FIT_LOCK:
        _FIT_CACHE.clear()


def _fit_cache_key(conn, lead: dict):
    p = conn.execute(
        "SELECT COUNT(*) c, COALESCE(MAX(id),0) m, COALESCE(SUM(status='available'),0) a FROM properties"
    ).fetchone()
    i = conn.execute("SELECT COUNT(*) c, COALESCE(MAX(id),0) m FROM installment_plans").fetchone()
    sig = tuple(lead.get(k) for k in _LEAD_FIT_FIELDS)
    return (lead["id"], sig, p["c"], p["m"], p["a"], i["c"], i["m"])


def _summarize_fit(results, lead: dict) -> dict:
    from matching import VIABLE_MIN_SCORE, has_plan_limits

    prefs = [l.strip() for l in (lead.get("preferred_locations") or "").split(",") if l.strip()]
    has_budget = bool(lead.get("budget_min") or lead.get("budget_max"))
    pref = lead.get("payment_preference")
    has_limits = has_plan_limits(lead)
    cash_buyer = pref == "cash"
    installment_buyer = (not cash_buyer) and (pref == "installment" or has_limits)

    # One consistent funnel, every step counted on the units that survived the previous one:
    #   location -> enough bedrooms -> priced inside the budget -> a plan that fits.
    # The final "matching units" number (qualified) can therefore never exceed the earlier steps,
    # and the three counts shown to the agent no longer contradict each other.
    loc_ok = [r for r in results if "location" not in r["hard_fail"]]
    beds_ok = [r for r in loc_ok if "bedrooms" not in r["hard_fail"]]
    budget_ok = [r for r in beds_ok if r["price_status"] in ("ok", "below", "soft_over")]
    plan_ok = [r for r in budget_ok if r["plan_fits"] is True]
    # "Matching unit" = no hard failure AND not far below the stated budget range: a 4.5M unit
    # for a 7-9M buyer is not a realistic match even though matching.py only soft-penalises it.
    qualified = [r for r in results if not r["hard_fail"] and r["price_status"] != "far_below"]
    viable = [r for r in qualified if r["score"] >= VIABLE_MIN_SCORE]
    best_overall = max((r["score"] for r in results), default=0.0)

    if not has_budget:
        budget_status = "unknown"
    else:
        budget_status = "fit" if budget_ok else "gap"
    if not prefs:
        location_status = "unknown"
    else:
        location_status = "fit" if loc_ok else "gap"
    if cash_buyer:
        financing_status = "fit"
    elif installment_buyer and has_limits:
        financing_status = "fit" if plan_ok else "gap"
    else:
        financing_status = "unknown"

    has_requirements = bool(has_budget or prefs or lead.get("min_bedrooms") or cash_buyer or installment_buyer)
    return {
        "total_scored": len(results),
        "qualified_count": len(qualified),
        "viable_count": len(viable),
        "best_score": best_overall,
        "location_count": len(loc_ok),
        "budget_count": len(budget_ok),
        "plan_count": len(plan_ok),
        "budget_status": budget_status,
        "location_status": location_status,
        "financing_status": financing_status,
        "has_limits": has_limits,
        "has_requirements": has_requirements,
    }


def get_fit_summary(lead_id: int) -> dict:
    """Live, cached summary of how the available inventory fits this lead's stated
    requirements. Treat the returned dict as read-only."""
    from matching import match_lead_to_properties

    conn = get_connection()
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Lead {lead_id} not found")
    lead = dict(row)
    key = _fit_cache_key(conn, lead)
    conn.close()

    with _FIT_LOCK:
        hit = _FIT_CACHE.get(key)
        if hit is not None:
            return hit
        results = match_lead_to_properties(lead_id, top_n=None, log_matches=False)
        summary = _summarize_fit(results, lead)
        for k in [k for k in _FIT_CACHE if k[0] == lead_id]:
            del _FIT_CACHE[k]                      # drop stale entries for this lead
        if len(_FIT_CACHE) >= _FIT_CACHE_MAX:
            _FIT_CACHE.pop(next(iter(_FIT_CACHE)))
        _FIT_CACHE[key] = summary
        return summary


# ---------------------------------------------------------------------------
# Feature computation for a real lead in the DB
# ---------------------------------------------------------------------------
def compute_features(lead_id: int, live_fit: bool = True):
    conn = get_connection()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        conn.close()
        raise ValueError(f"Lead {lead_id} not found")
    lead = dict(lead)

    events = [dict(r) for r in conn.execute(
        "SELECT * FROM lead_events WHERE lead_id = ? ORDER BY created_at ASC", (lead_id,)
    ).fetchall()]

    best_match = conn.execute(
        "SELECT MAX(score) as m FROM matches WHERE lead_id = ?", (lead_id,)
    ).fetchone()
    best_match_score = (best_match["m"] or 0.5) * 100 if best_match and best_match["m"] is not None else 60.0

    conn.close()

    if live_fit:
        # use the real current top match instead of whatever happened to be logged
        try:
            best_match_score = float(get_fit_summary(lead_id)["best_score"])
        except Exception:
            logger.warning("live fit unavailable for lead %s; using legacy best-match value", lead_id, exc_info=True)

    num_events = max(len(events), 1)
    num_property_views = sum(1 for e in events if e["event_type"] == "property_viewed")
    num_viewing_requests = sum(1 for e in events if e["event_type"] == "viewing_booked")

    if len(events) >= 2:
        # crude proxy: gap between first two events, in hours
        from datetime import datetime
        t0 = datetime.fromisoformat(events[0]["created_at"])
        t1 = datetime.fromisoformat(events[1]["created_at"])
        hours_to_first_response = max((t1 - t0).total_seconds() / 3600, 0.01)
    else:
        hours_to_first_response = 24.0  # unknown -> assume average

    from datetime import datetime
    created = datetime.fromisoformat(lead["created_at"])
    days_since_created = max((datetime.now() - created).total_seconds() / 86400, 0)

    features = [
        days_since_created,
        hours_to_first_response,
        num_events,
        num_property_views,
        num_viewing_requests,
        best_match_score,
        float(bool(lead["budget_min"] and lead["budget_max"])),
        float(bool(lead["payment_preference"] and lead["payment_preference"] != "unknown")),
        TIMELINE_URGENCY.get(lead["purchase_timeline"], 0.1),
        float(lead["channel"] == "whatsapp"),
    ]
    return features, lead


def score_lead(lead_id: int):
    bundle = _load_model()
    model, means, stds = bundle["model"], bundle["feature_means"], bundle["feature_stds"]
    correlations = bundle.get("feature_correlations", np.ones(len(FEATURE_NAMES)))

    features, lead = compute_features(lead_id)
    X = np.array([features])
    prob = model.predict_proba(X)[0][1]
    score = round(prob * 100, 1)

    # The model never sees hard requirements directly, so cap the score when the live
    # fit check finds that NO available unit meets the lead's stated requirements.
    raw_model_score = score
    score_capped = False
    try:
        fit = get_fit_summary(lead_id)
    except Exception:
        logger.warning("fit check failed for lead %s; the no-fit score cap was NOT applied "
                       "(is app/matching.py the latest version?)", lead_id, exc_info=True)
        fit = None
    if fit and fit["has_requirements"] and fit["qualified_count"] == 0 and score > NO_FIT_SCORE_CAP:
        score = NO_FIT_SCORE_CAP
        score_capped = True

    # Lightweight per-lead explanation: (value - mean)/std * feature_importance,
    # then re-signed by the feature's correlation with the outcome so "positive"
    # always means "pushed the score up", not just "above average".
    z = (X[0] - means) / stds
    importances = model.feature_importances_
    contributions = z * importances * np.sign(correlations)
    order = np.argsort(-np.abs(contributions))

    top_factors = []
    for i in order[:4]:
        direction = "positive" if contributions[i] > 0 else "negative"
        top_factors.append({
            "factor": FEATURE_NAMES[i],
            "value": round(float(features[i]), 2),
            "direction": direction,
        })

    model_version = bundle.get("model_version", MODEL_VERSION)
    is_empirical = bundle.get("empirical_probability_available", False)
    scoring_mode = "empirical_ml" if is_empirical else "heuristic_index"
    disclaimer = (
        "Empirically calibrated conversion likelihood trained on real brokerage outcomes."
        if is_empirical
        else "Heuristic Lead Quality Index (0-100). Empirical conversion probability is not claimed until >=25 real outcomes are recorded."
    )

    conn = get_connection()
    conn.execute("""
        INSERT INTO lead_scores (lead_id, score, conversion_probability, top_factors, model_version)
        VALUES (?,?,?,?,?)
    """, (lead_id, score, float(prob), json.dumps(top_factors), model_version))
    conn.commit()
    conn.close()

    return {
        "lead_id": lead_id,
        "score": score,
        "raw_model_score": raw_model_score,
        "score_capped": score_capped,
        "conversion_probability": round(float(prob), 3),
        "top_factors": top_factors,
        "model_version": model_version,
        "scoring_mode": scoring_mode,
        "empirical_probability_available": is_empirical,
        "probability_disclaimer": disclaimer,
    }


FACTOR_LABELS = {
    "days_since_created": "time since lead was created",
    "hours_to_first_response": "response speed",
    "num_events": "overall engagement",
    "num_property_views": "properties viewed",
    "num_viewing_requests": "viewings requested",
    "best_match_score": "inventory fit",
    "budget_defined": "budget clarity",
    "payment_preference_known": "payment preference stated",
    "timeline_urgency_score": "stated urgency",
    "channel_whatsapp": "WhatsApp channel",
}


def humanize_factors(top_factors):
    out = []
    for f in top_factors:
        label = FACTOR_LABELS.get(f["factor"], f["factor"])
        sign = "(+) helping" if f["direction"] == "positive" else "(-) hurting"
        out.append(f"{label} {sign}")
    return out


# ---------------------------------------------------------------------------
# Lead Intelligence Report
# Wraps score_lead() + matching + extraction helpers into one rich dict.
# Does NOT replace or duplicate score_lead — it calls it and enriches the output.
# ---------------------------------------------------------------------------
def _resolve_urgency(lead: dict):
    """leads.urgency defaults to 'unknown'; that must not shadow a real purchase_timeline
    (this was why 'Purchase timeline not stated' showed up for timeline='immediate')."""
    explicit = (lead.get("urgency") or "").strip().lower()
    timeline = (lead.get("purchase_timeline") or "").strip().lower()
    timeline_map = {"immediate": "high", "1-3m": "high", "3-6m": "medium", "6m+": "low"}
    if explicit in ("high", "medium", "low"):
        return explicit, (timeline if timeline in timeline_map else explicit)
    if timeline in timeline_map:
        return timeline_map[timeline], timeline
    return "unknown", "unknown"


def get_lead_intelligence(lead_id: int) -> dict:
    """
    Returns a rich AI Lead Intelligence report for the given lead_id.

    Each fit dimension has a boolean (`budget_fit`, ...) kept for backward
    compatibility and a three-state `*_status` ('fit' | 'gap' | 'unknown'), so the UI
    can tell "does not fit" apart from "not stated yet".

    Fit is computed LIVE from the matching engine (see get_fit_summary), never from
    the `matches` log table. If no available unit meets the lead's stated
    requirements, the score is capped (NO_FIT_SCORE_CAP) and says so.

    Never claims empirical conversion probability unless the model has been trained
    on >= 25 real outcomes (empirical_probability_available=True).
    """
    from extraction import compute_missing_fields, suggest_next_question, CORE_FIELDS

    # --- Core score (already includes the no-fit cap) and live inventory fit ---
    score_result = score_lead(lead_id)
    fit = get_fit_summary(lead_id)

    conn = get_connection()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        conn.close()
        raise ValueError(f"Lead {lead_id} not found")
    lead = dict(lead)

    events = [dict(r) for r in conn.execute(
        "SELECT * FROM lead_events WHERE lead_id = ? ORDER BY created_at ASC", (lead_id,)
    ).fetchall()]

    # --- Available inventory per preferred location ---
    preferred_locs = [l.strip() for l in (lead.get("preferred_locations") or "").split(",") if l.strip()]
    loc_counts = {}
    for loc in preferred_locs:
        loc_counts[loc] = conn.execute(
            "SELECT COUNT(*) c FROM properties WHERE location = ? AND status = 'available'", (loc,)
        ).fetchone()["c"]
    conn.close()

    match_count = fit["qualified_count"]          # units meeting ALL stated hard requirements
    best_match_score = round(fit["best_score"], 1)
    max_monthly = lead.get("max_monthly_payment")
    max_down = lead.get("max_down_payment")

    # --- Requirement completeness ---
    missing = compute_missing_fields(lead)
    next_q = suggest_next_question(lead)
    total_core = len(CORE_FIELDS)
    filled = total_core - len(missing)
    requirement_completeness = round(filled / total_core, 2)  # 0.0-1.0

    # --- Engagement signal ---
    non_system_events = [e for e in events if not e["event_type"].startswith("next_action")]
    num_events = len(non_system_events)
    num_property_views = sum(1 for e in non_system_events if e["event_type"] == "property_viewed")
    num_viewing_requests = sum(1 for e in non_system_events if e["event_type"] == "viewing_booked")
    engagement_score = min(1.0, (num_events * 0.05) + (num_property_views * 0.1) + (num_viewing_requests * 0.3))
    if engagement_score >= 0.7 or num_viewing_requests >= 1:
        engagement_label = "high"
    elif engagement_score >= 0.3 or num_property_views >= 2:
        engagement_label = "medium"
    elif num_events >= 2:
        engagement_label = "low"
    else:
        engagement_label = "very_low"

    urgency_label, urgency_source = _resolve_urgency(lead)

    positive_factors = []
    warnings = []
    score = score_result["score"]

    if score >= 70:
        positive_factors.append(f"High lead quality index ({score}/100)")
    elif score >= 45:
        positive_factors.append(f"Moderate lead quality index ({score}/100)")
    else:
        warnings.append(f"Low lead quality index ({score}/100)")
    if score_result.get("score_capped"):
        warnings.append(
            f"Score capped at {NO_FIT_SCORE_CAP:.0f}/100: no available unit meets all stated requirements "
            f"(model score was {score_result['raw_model_score']})")

    # --- Budget fit (units in the preferred areas priced inside the stated budget) ---
    budget_status = fit["budget_status"]
    budget_fit = budget_status == "fit"
    if budget_status == "fit":
        n = fit["budget_count"]
        budget_fit_reason = (
            f"{n} {'unit' if n == 1 else 'units'} in your preferred area with enough bedrooms "
            f"{'is' if n == 1 else 'are'} priced within your budget"
            + (f" (best match score: {best_match_score}%)" if best_match_score > 0 else "")
        )
        positive_factors.append(budget_fit_reason)
    elif budget_status == "gap":
        budget_fit_reason = "No properties currently match the stated budget"
        warnings.append(budget_fit_reason)
    else:
        budget_fit_reason = "Budget not stated"
        warnings.append(budget_fit_reason)

    # --- Location fit ---
    location_status = fit["location_status"]
    location_fit = location_status == "fit"
    location_fit_reason = "No location preference stated"
    if loc_counts:
        available_locs = [loc for loc, cnt in loc_counts.items() if cnt > 0]
        unavailable_locs = [loc for loc, cnt in loc_counts.items() if cnt == 0]
        if available_locs:
            location_fit_reason = f"Inventory available in: {', '.join(available_locs)}"
            positive_factors.append(location_fit_reason)
        if unavailable_locs:
            warnings.append(f"No available inventory in: {', '.join(unavailable_locs)}")
        if not available_locs:
            location_fit_reason = "No inventory in any preferred location"
    else:
        warnings.append(location_fit_reason)

    # --- Financing fit (a plan that fits, on units inside the area + budget) ---
    financing_status = fit["financing_status"]
    financing_fit = financing_status == "fit"
    if lead.get("payment_preference") == "cash":
        financing_fit_reason = "Cash buyer — no installment constraint to check"
    elif financing_status == "fit":
        financing_fit_reason = (
            f"At least one available property has a plan within "
            f"{'monthly payment of {:,.0f} EGP'.format(max_monthly) if max_monthly else ''}"
            f"{' and ' if max_monthly and max_down else ''}"
            f"{'down payment of {:,.0f} EGP'.format(max_down) if max_down else ''}"
            f" ({fit['plan_count']} {'unit' if fit['plan_count'] == 1 else 'units'} "
            f"in your area, bedrooms and budget)"
        )
    elif financing_status == "gap":
        financing_fit_reason = "No available property plan fits stated constraints — may need to re-qualify"
    else:
        financing_fit_reason = "No financing constraints stated"
    if financing_status == "fit":
        positive_factors.append(f"Financing fit: {financing_fit_reason}")
    elif financing_status == "gap":
        warnings.append(f"Financing gap: {financing_fit_reason}")

    # --- Payment preference ---
    if lead.get("payment_preference") and lead["payment_preference"] not in ("unknown", None, ""):
        positive_factors.append(f"Payment preference stated: {lead['payment_preference']}")
    else:
        warnings.append("Cash vs. installment preference not stated")

    # --- Urgency ---
    if urgency_label == "high":
        positive_factors.append(f"High urgency: timeline '{urgency_source}'")
    elif urgency_label == "medium":
        positive_factors.append(f"Medium urgency: timeline '{urgency_source}'")
    elif urgency_label == "unknown":
        warnings.append("Purchase timeline not stated")

    # --- Engagement ---
    if engagement_label in ("high", "medium"):
        positive_factors.append(f"{engagement_label.capitalize()} engagement ({num_events} interactions, {num_property_views} views)")
    else:
        warnings.append(f"Low engagement ({num_events} interactions)")

    if requirement_completeness < 0.5:
        warnings.append(f"Incomplete profile: {len(missing)} of {total_core} core fields missing")

    # --- Priority tier ---
    if score >= 75:
        priority, priority_emoji = "high", "\U0001f525"
    elif score >= 50:
        priority, priority_emoji = "medium", "\U0001f7e1"
    else:
        priority, priority_emoji = "low", "⚪"

    # --- Explanation (human-readable summary) ---
    name = lead.get("name", "This lead")
    explanation_parts = [f"{name} is a {priority}-priority lead (score {score}/100)"
                         if priority != "low" else f"{name} is currently low priority (score {score}/100)"]
    if score_result.get("score_capped"):
        explanation_parts.append("no available unit meets all stated requirements yet")
    if budget_fit:
        explanation_parts.append(f"budget fits {fit['budget_count']} propert{'y' if fit['budget_count'] == 1 else 'ies'}")
    if location_fit:
        explanation_parts.append("inventory available in preferred locations")
    if financing_fit and lead.get("payment_preference") == "installment":
        explanation_parts.append("financing constraints are achievable")
    if urgency_label == "high":
        explanation_parts.append("high urgency")
    if missing:
        explanation_parts.append(f"{len(missing)} profile field(s) still missing")
    explanation = ". ".join(explanation_parts[:4]) + "."

    return {
        "lead_id":                          lead_id,
        "lead_name":                        lead.get("name"),
        "score":                            score,
        "raw_model_score":                  score_result.get("raw_model_score", score),
        "score_capped":                     score_result.get("score_capped", False),
        "priority":                         priority,
        "priority_emoji":                   priority_emoji,
        "explanation":                      explanation,
        "scoring_mode":                     score_result["scoring_mode"],
        "empirical_probability_available":  score_result["empirical_probability_available"],
        "probability_disclaimer":           score_result["probability_disclaimer"],
        "budget_fit":                   budget_fit,
        "budget_status":                budget_status,
        "budget_fit_reason":            budget_fit_reason,
        "location_fit":                 location_fit,
        "location_status":              location_status,
        "location_fit_reason":          location_fit_reason,
        "financing_fit":                financing_fit,
        "financing_status":             financing_status,
        "financing_fit_reason":         financing_fit_reason,
        "inventory_availability":       {"total_matches": match_count, "viable_matches": fit["viable_count"],
                                         "by_location": loc_counts},
        "engagement":                   {"label": engagement_label, "score": round(engagement_score, 2),
                                         "events": num_events, "property_views": num_property_views,
                                         "viewing_requests": num_viewing_requests},
        "urgency":                      {"label": urgency_label, "source": urgency_source},
        "requirement_completeness":     {"ratio": requirement_completeness,
                                         "filled": filled, "total": total_core},
        "positive_factors":             positive_factors,
        "warnings":                     warnings,
        "missing_info":                 missing,
        "next_question":                next_q,
        "top_factors":                  score_result["top_factors"],
        "top_factors_readable":         humanize_factors(score_result["top_factors"]),
        "match_count":                  match_count,
        "best_match_score":             best_match_score,
    }


def top_factors_from_result(score_result):
    """Helper: returns top_factors list from a score_lead() result dict."""
    return score_result.get("top_factors", [])


if __name__ == "__main__":
    train_model()
    print("\n--- scoring sample leads (run seed.py first) ---")
    conn = get_connection()
    ids = [r["id"] for r in conn.execute("SELECT id FROM leads").fetchall()]
    conn.close()
    for lid in ids:
        result = score_lead(lid)
        print(f"\nLead {lid}: score={result['score']} prob={result['conversion_probability']}")
        for h in humanize_factors(result["top_factors"]):
            print(f"   - {h}")