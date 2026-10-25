"""
Phase 5 — Feedback loop.

Agents log what actually happened after a next-best-action was suggested
(contacted, viewing booked, won, lost, no response). This closes the loop:
  recommendation -> agent action -> real outcome -> retrain -> better recommendation

`retrain_from_outcomes()` is the honest version of Phase 5: with only a
handful of demo leads there isn't enough real signal to retrain a model from
scratch, so what it actually does is:
  1. pull real logged outcomes from the `outcomes` table
  2. convert each into a synthetic-style training row using the SAME feature
     computation as scoring.py (so it's apples-to-apples with the synthetic set)
  3. blend those real rows into the synthetic dataset, weighted more heavily
     than synthetic rows, and retrain

This means: the more real outcomes get logged, the more the model's behavior
is actually driven by your brokerage's real data instead of the synthetic
prior — which is the correct shape of this problem even with few examples.
"""
import numpy as np
import pickle
from db import get_connection
from scoring import (
    generate_synthetic_dataset, compute_features, train_model, train_empirical_model,
    MODEL_PATH, FEATURE_NAMES,
)
from sklearn.ensemble import GradientBoostingClassifier

OUTCOME_TO_LABEL = {
    "won": 1, "viewing_booked": 1, "negotiating": 1,
    "lost": 0, "no_response": 0,
}


def log_outcome(lead_id: int, outcome: str, note: str = "", property_id: int = None):
    conn = get_connection()
    conn.execute("""
        INSERT INTO outcomes (lead_id, outcome, note, property_id) VALUES (?,?,?,?)
    """, (lead_id, outcome, note, property_id))
    status_map = {"won": "won", "lost": "lost", "viewing_booked": "viewing_booked",
                  "negotiating": "negotiating", "contacted": "contacted", "no_response": "contacted"}
    if outcome in status_map:
        conn.execute("UPDATE leads SET status = ?, updated_at = datetime('now') WHERE id = ?",
                      (status_map[outcome], lead_id))
    conn.commit()
    conn.close()


def get_real_training_rows():
    conn = get_connection()
    rows = conn.execute("""
        SELECT lead_id, outcome FROM outcomes
        WHERE outcome IN ('won','lost','viewing_booked','negotiating','no_response')
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()

    X, y = [], []
    seen_leads = set()
    for r in rows:
        if r["lead_id"] in seen_leads:
            continue  # one label per lead — most recent outcome wins
        seen_leads.add(r["lead_id"])
        try:
            features, _ = compute_features(r["lead_id"])
        except ValueError:
            continue
        X.append(features)
        y.append(OUTCOME_TO_LABEL[r["outcome"]])
    return np.array(X) if X else np.empty((0, len(FEATURE_NAMES))), np.array(y)


def retrain_from_outcomes(real_weight: int = 8, min_real_for_pure_empirical: int = 25):
    """
    Retrains the scoring model from logged outcomes.
    If >= min_real_for_pure_empirical (25) real outcomes exist, it trains a pure empirical
    model solely on real data (zero synthetic rows).
    If fewer real outcomes exist, it blends them with the synthetic prior but explicitly
    labels the model as heuristic guidance (refusing to claim empirical probability).
    """
    X_real, y_real = get_real_training_rows()

    if len(X_real) >= min_real_for_pure_empirical:
        print(f"✓ Reached {len(X_real)} real outcomes! Training pure empirical conversion model (zero synthetic rows).")
        return train_empirical_model(min_outcomes=min_real_for_pure_empirical)

    if len(X_real) == 0:
        print("No real outcomes logged yet — maintaining heuristic prior model.")
        return train_model()

    X_synth, y_synth = generate_synthetic_dataset()
    X_real_weighted = np.repeat(X_real, real_weight, axis=0)
    y_real_weighted = np.repeat(y_real, real_weight, axis=0)

    X = np.vstack([X_synth, X_real_weighted])
    y = np.concatenate([y_synth, y_real_weighted])
    perm = np.random.RandomState(7).permutation(len(X))
    X, y = X[perm], y[perm]

    split = int(len(X) * 0.85)
    model = GradientBoostingClassifier(n_estimators=150, max_depth=3, learning_rate=0.08, random_state=7)
    model.fit(X[:split], y[:split])
    test_acc = model.score(X[split:], y[split:])

    correlations = np.array([
        np.corrcoef(X[:split, i], y[:split])[0, 1] if X[:split, i].std() > 0 else 0.0
        for i in range(X.shape[1])
    ])
    correlations = np.nan_to_num(correlations)

    bundle = {
        "model": model,
        "feature_means": X[:split].mean(axis=0),
        "feature_stds": X[:split].std(axis=0) + 1e-6,
        "feature_correlations": correlations,
        "model_version": f"v1-pilot-hybrid-N{len(X_real)}",
        "model_type": "heuristic_prior_with_feedback",
        "empirical_probability_available": False,
        "sample_size": len(X_real),
    }

    import os
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(bundle, f)

    print(f"Retrained with {len(X_real)} real outcome(s) (weighted x{real_weight}) "
          f"blended into {len(X_synth)} synthetic rows. (Heuristic guidance — pure empirical model unlocks at 25 real outcomes).")
    return model


# ---------------------------------------------------------------------------
# Model reliability / data maturity
#
# Diagnostic finding this exists to communicate honestly (see CHANGELOG.md):
# when we tested the outcomes -> retraining -> model loop with 4 real
# outcomes, the real-data effect was measurable and clearly distinguishable
# from retraining noise, BUT individual-lead score direction was not fully
# reliable — one lead's own "won" label pulled its score down, not up.
# That's expected behavior with this little real data, not a bug, and the
# system should say so rather than let a score look more authoritative than
# it currently is.
#
# The thresholds below are practical engineering stages for THIS prototype,
# chosen to give honest, increasingly confident language as real outcomes
# accumulate. They are NOT statistically derived or validated cutoffs — do
# not present them as such, and do not tune them to make current numbers
# look better.
# ---------------------------------------------------------------------------
MATURITY_STAGES = [
    (0, 9, "VERY_LOW_DATA", "Very Low",
     "Scores are directional only. The model is trained almost entirely on synthetic data — "
     "individual lead scores should not be treated as reliable predictions yet."),
    (10, 24, "EARLY_PILOT", "Low",
     "A small number of real outcomes are now influencing the model, but individual-lead "
     "score direction is still not consistently reliable. Treat scores as directional guidance."),
    (25, 49, "PILOT", "Moderate",
     "Real outcomes are meaningfully shaping the model. Scores are more trustworthy in "
     "aggregate (e.g. ranking leads) than as precise individual probabilities."),
    (50, 99, "VALIDATION", "Good",
     "Approaching the recommended validation target. Scores are reasonably trustworthy, "
     "though still worth sanity-checking against agent judgment on individual leads."),
    (100, float("inf"), "PRODUCTION_CANDIDATE", "Higher",
     "Enough real outcomes exist to treat the model as a genuine production candidate. "
     "Still recommend periodic validation against actual outcomes as more data arrives."),
]


def get_model_maturity(real_outcome_count: int) -> dict:
    """Returns structured reliability information for the CURRENT number of real
    outcomes in the system. Does not change the scoring model or its output —
    purely descriptive metadata about how much to trust it right now."""
    for low, high, label, reliability, warning in MATURITY_STAGES:
        if low <= real_outcome_count <= high:
            return {
                "real_outcome_count": real_outcome_count,
                "maturity": label,
                "reliability": reliability,
                "warning": warning,
            }
    # Unreachable given the stages above cover 0..inf, but fail safe rather than crash
    return {
        "real_outcome_count": real_outcome_count,
        "maturity": "UNKNOWN",
        "reliability": "Unknown",
        "warning": "Unable to determine model maturity.",
    }


# ---------------------------------------------------------------------------
# Manager-facing pipeline summary
# ---------------------------------------------------------------------------
def pipeline_summary():
    conn = get_connection()
    leads = [dict(r) for r in conn.execute("SELECT * FROM leads").fetchall()]

    latest_scores = {}
    for r in conn.execute("""
        SELECT lead_id, score, conversion_probability, MAX(created_at)
        FROM lead_scores GROUP BY lead_id
    """).fetchall():
        latest_scores[r["lead_id"]] = {"score": r["score"], "probability": r["conversion_probability"]}

    status_counts = {}
    total_pipeline_value = 0.0
    weighted_forecast = 0.0
    for lead in leads:
        status_counts[lead["status"]] = status_counts.get(lead["status"], 0) + 1
        est_value = lead["budget_max"] or lead["budget_min"] or 0
        total_pipeline_value += est_value
        prob = latest_scores.get(lead["id"], {}).get("probability", 0.3)
        weighted_forecast += est_value * prob

    conn.close()
    return {
        "total_leads": len(leads),
        "status_breakdown": status_counts,
        "total_pipeline_value_egp": round(total_pipeline_value, -3),
        "probability_weighted_forecast_egp": round(weighted_forecast, -3),
        "leads_scored": len(latest_scores),
    }


if __name__ == "__main__":
    # simulate some outcomes for the demo
    log_outcome(1, "viewing_booked", "Booked for Sat 10am")
    log_outcome(3, "won", "Signed contract on Hacienda Bay villa", property_id=None)
    log_outcome(2, "no_response", "No reply after 3 follow-ups")

    print("=== Pipeline summary ===")
    import json
    print(json.dumps(pipeline_summary(), indent=2))

    print("\n=== Retraining from outcomes ===")
    retrain_from_outcomes()
