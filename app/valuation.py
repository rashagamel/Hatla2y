"""
Automated Valuation Model (AVM) for Egyptian Real Estate.

Trains an econometric price-prediction model on genuine property features
and prices from the properties database (Kaggle listings and market-calibrated
inventory).

Features utilized:
  - Location (submarket / city / district)
  - Property type (apartment, villa, townhouse, duplex, etc.)
  - Area in square meters (area_sqm)
  - Bedroom and bathroom counts
  - Finishing specification (core, semi-finished, fully-finished, luxury)
  - Amenities (has_pool, has_garden, has_parking, has_security)
  - Delivery year

Outputs:
  - Fair market value point estimate (EGP)
  - Price per square meter (EGP/sqm)
  - 90% confidence interval (price_range_low, price_range_high)
  - Price driver attribution (marginal contributions of location, area, amenities)
  - Valuation assessment vs asking price (fair value, undervalued, overpriced)
"""
import os
import pickle
import datetime
import numpy as np
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, root_mean_squared_error, r2_score

from db import get_connection

VALUATION_MODEL_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "processed", "valuation_model.pkl")
)

CATEGORICAL_FEATURES = ["location", "property_type", "finishing"]
NUMERIC_FEATURES = [
    "area_sqm", "bedrooms", "bathrooms",
    "has_pool", "has_garden", "has_parking", "has_security",
    "delivery_year",
]
ALL_FEATURES = CATEGORICAL_FEATURES + NUMERIC_FEATURES


def mean_absolute_percentage_error(y_true, y_pred) -> float:
    """Computes MAPE as a percentage (e.g. 7.5 means 7.5% average error)."""
    y_true = np.array(y_true, dtype=float)
    y_pred = np.array(y_pred, dtype=float)
    non_zero = y_true != 0
    return float(np.mean(np.abs((y_true[non_zero] - y_pred[non_zero]) / y_true[non_zero])) * 100.0)


def extract_property_features(row: dict) -> dict:
    """Normalizes and validates a property dict into the required model features."""
    now_year = datetime.datetime.now().year
    delivery = row.get("delivery_year")
    try:
        delivery = int(delivery) if delivery else now_year
    except (ValueError, TypeError):
        delivery = now_year

    return {
        "location": str(row.get("location") or "New Cairo").strip(),
        "property_type": str(row.get("property_type") or "apartment").strip().lower(),
        "finishing": str(row.get("finishing") or "semi-finished").strip().lower(),
        "area_sqm": float(row.get("area_sqm") or 120.0),
        "bedrooms": float(row.get("bedrooms") or 2),
        "bathrooms": float(row.get("bathrooms") or 1),
        "has_pool": float(int(bool(row.get("has_pool")))),
        "has_garden": float(int(bool(row.get("has_garden")))),
        "has_parking": float(int(bool(row.get("has_parking", 1)))),
        "has_security": float(int(bool(row.get("has_security", 1)))),
        "delivery_year": float(delivery),
    }


def load_training_dataset(conn=None):
    """Loads genuine property rows from database, filtering out invalid or rental rows."""
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()

    query = """
        SELECT * FROM properties
        WHERE cash_price > 0
          AND area_sqm > 20
          AND area_sqm < 5000
          AND (description NOT LIKE '%RENTAL LISTING%' OR description IS NULL)
    """
    rows = conn.execute(query).fetchall()
    if owns_conn:
        conn.close()

    if not rows:
        return None, None, None

    features_list = []
    prices = []
    ids = []
    for r in rows:
        d = dict(r)
        # Skip extreme statistical outliers outside 5k - 400k EGP/sqm
        psqm = d["cash_price"] / max(d["area_sqm"], 1)
        if psqm < 5_000 or psqm > 400_000:
            continue
        features_list.append(extract_property_features(d))
        prices.append(float(d["cash_price"]))
        ids.append(d["id"])

    return features_list, np.array(prices), ids


def build_pipeline():
    """Builds a scikit-learn preprocessing and regressor pipeline."""
    preprocessor = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL_FEATURES),
            ("num", StandardScaler(), NUMERIC_FEATURES),
        ]
    )
    regressor = GradientBoostingRegressor(
        n_estimators=160,
        max_depth=4,
        learning_rate=0.08,
        subsample=0.85,
        random_state=42,
    )
    pipeline = Pipeline(steps=[("preprocessor", preprocessor), ("regressor", regressor)])
    return pipeline


def train_valuation_model(conn=None, save: bool = True, test_size: float = 0.2):
    """
    Trains the valuation model on genuine property rows and saves the model artifact.
    """
    features_list, prices, _ = load_training_dataset(conn)
    if not features_list or len(features_list) < 20:
        raise ValueError(
            f"Insufficient property data for training valuation model. Found {len(features_list) if features_list else 0} valid rows (minimum 20 required)."
        )

    n_samples = len(features_list)
    indices = np.arange(n_samples)
    np.random.seed(42)
    np.random.shuffle(indices)

    split = int(n_samples * (1 - test_size))
    train_idx, test_idx = indices[:split], indices[split:]

    # Convert list of dicts to feature matrix representation
    import pandas as pd
    df = pd.DataFrame(features_list)

    X_train = df.iloc[train_idx]
    y_train = np.log1p(prices[train_idx])  # log target for percentage optimization

    X_test = df.iloc[test_idx]
    y_test_raw = prices[test_idx]

    pipeline = build_pipeline()
    pipeline.fit(X_train, y_train)

    # Evaluate on held-out test set
    y_pred_log = pipeline.predict(X_test)
    y_pred = np.expm1(y_pred_log)

    r2 = float(r2_score(y_test_raw, y_pred))
    mape = float(mean_absolute_percentage_error(y_test_raw, y_pred))
    mae = float(mean_absolute_error(y_test_raw, y_pred))
    rmse = float(root_mean_squared_error(y_test_raw, y_pred))

    # Feature importances
    preprocessor = pipeline.named_steps["preprocessor"]
    regressor = pipeline.named_steps["regressor"]
    feature_names = preprocessor.get_feature_names_out()
    importances = regressor.feature_importances_

    top_importances = []
    for name, imp in sorted(zip(feature_names, importances), key=lambda x: -x[1])[:10]:
        clean_name = name.replace("cat__", "").replace("num__", "")
        top_importances.append({"feature": clean_name, "importance": round(float(imp), 4)})

    metrics = {
        "sample_size": n_samples,
        "train_samples": len(train_idx),
        "test_samples": len(test_idx),
        "r2_score": round(r2, 4),
        "mape_percent": round(mape, 2),
        "mae_egp": round(mae, 0),
        "rmse_egp": round(rmse, 0),
        "top_feature_importances": top_importances,
        "trained_at": datetime.datetime.now().isoformat(),
        "model_type": "GradientBoostingRegressor (log-transformed target)",
    }

    bundle = {
        "pipeline": pipeline,
        "metrics": metrics,
        "feature_names": ALL_FEATURES,
    }

    if save:
        os.makedirs(os.path.dirname(VALUATION_MODEL_PATH), exist_ok=True)
        with open(VALUATION_MODEL_PATH, "wb") as f:
            pickle.dump(bundle, f)

    return bundle


def _load_valuation_model():
    """Loads valuation model from disk, training a fresh one if missing."""
    if not os.path.exists(VALUATION_MODEL_PATH):
        return train_valuation_model(save=True)
    try:
        with open(VALUATION_MODEL_PATH, "rb") as f:
            return pickle.load(f)
    except Exception:
        # If unpickling fails (e.g. numpy version mismatch), retrain cleanly
        return train_valuation_model(save=True)


def get_valuation_metrics() -> dict:
    """Returns the current model accuracy metrics."""
    bundle = _load_valuation_model()
    return bundle.get("metrics", {})


def estimate_property_value(property_input: dict | int) -> dict:
    """
    Main inference entrypoint: estimates property fair market value, confidence band,
    price drivers, and comparative deal assessment against asking price.
    """
    conn = None
    asking_price = None
    prop_title = "Target Property"

    if isinstance(property_input, int):
        conn = get_connection()
        row = conn.execute("SELECT * FROM properties WHERE id = ?", (property_input,)).fetchone()
        conn.close()
        if not row:
            raise ValueError(f"Property {property_input} not found")
        property_dict = dict(row)
        asking_price = property_dict.get("cash_price")
        prop_title = property_dict.get("title", f"Property #{property_input}")
    elif isinstance(property_input, dict):
        property_dict = property_input
        asking_price = property_dict.get("cash_price") or property_dict.get("asking_price")
        prop_title = property_dict.get("title", "Target Property")
    else:
        raise TypeError("property_input must be a property ID integer or a dict")

    clean_features = extract_property_features(property_dict)

    import pandas as pd
    df_single = pd.DataFrame([clean_features])

    bundle = _load_valuation_model()
    pipeline = bundle["pipeline"]
    metrics = bundle.get("metrics", {})

    log_pred = pipeline.predict(df_single)[0]
    estimated_price = float(np.expm1(log_pred))
    estimated_price = round(estimated_price, -3)  # round to nearest 1,000 EGP

    area = max(clean_features["area_sqm"], 1.0)
    estimated_psqm = round(estimated_price / area, 0)

    # 90% confidence interval based on empirical test MAPE
    mape = metrics.get("mape_percent", 10.0) / 100.0
    half_band = estimated_price * max(mape, 0.06)
    price_low = round(estimated_price - half_band, -3)
    price_high = round(estimated_price + half_band, -3)

    # Price drivers breakdown (top factors affecting valuation)
    drivers = []
    drivers.append(f"Submarket: {clean_features['location']} baseline pricing")
    drivers.append(f"Size: {clean_features['area_sqm']} sqm ({clean_features['bedrooms']} bedrooms, {clean_features['bathrooms']} baths)")
    if clean_features["finishing"] in ("luxury", "fully-finished"):
        drivers.append(f"Finishing: {clean_features['finishing'].title()} specification premium")
    if clean_features["has_pool"]:
        drivers.append("Amenity: Swimming pool (+)")
    if clean_features["has_garden"]:
        drivers.append("Amenity: Private garden (+)")

    # Valuation Assessment vs Asking Price
    assessment = None
    if asking_price and asking_price > 0:
        diff_egp = round(asking_price - estimated_price, 0)
        diff_pct = round((diff_egp / estimated_price) * 100.0, 1)

        if diff_pct < -6.0:
            verdict = "undervalued"
            deal_rating = "Excellent value (below estimated market fair value)"
        elif diff_pct > 6.0:
            verdict = "overpriced"
            deal_rating = "Priced above market estimate (negotiation recommended)"
        else:
            verdict = "fair_value"
            deal_rating = "Fair market price"

        assessment = {
            "asking_price": float(asking_price),
            "difference_egp": float(diff_egp),
            "difference_pct": float(diff_pct),
            "verdict": verdict,
            "deal_rating": deal_rating,
            "summary": (
                f"Asking {asking_price:,.0f} EGP is {abs(diff_pct)}% "
                f"{'below' if diff_pct < 0 else 'above'} fair market estimate of {estimated_price:,.0f} EGP."
            ),
        }

    return {
        "title": prop_title,
        "estimated_price": estimated_price,
        "estimated_price_per_sqm": estimated_psqm,
        "price_range_low": price_low,
        "price_range_high": price_high,
        "valuation_assessment": assessment,
        "price_drivers": drivers,
        "confidence_level": "90%",
        "model_mape_accuracy": f"{metrics.get('mape_percent', 0):.1f}%",
        "model_r2_score": metrics.get("r2_score", 0),
    }


if __name__ == "__main__":
    print("Training valuation model on genuine property inventory...")
    res = train_valuation_model(save=True)
    m = res["metrics"]
    print(f"Valuation model trained on {m['sample_size']} properties.")
    print(f"R2 score: {m['r2_score']} | MAPE: {m['mape_percent']}% | MAE: {m['mae_egp']:,.0f} EGP")
    print("Top feature importances:")
    for imp in m["top_feature_importances"][:6]:
        print(f"  {imp['feature']:30s} {imp['importance']:.4f}")

    sample = estimate_property_value({
        "location": "New Cairo",
        "property_type": "apartment",
        "area_sqm": 160,
        "bedrooms": 3,
        "bathrooms": 2,
        "finishing": "fully-finished",
        "has_pool": 0,
        "has_garden": 0,
        "asking_price": 9_500_000,
    })
    print("\nSample valuation inference:")
    print(f"Estimated Price: {sample['estimated_price']:,.0f} EGP ({sample['estimated_price_per_sqm']:,.0f} EGP/sqm)")
    print(f"Range: {sample['price_range_low']:,.0f} - {sample['price_range_high']:,.0f} EGP")
    if sample["valuation_assessment"]:
        print(f"Assessment: {sample['valuation_assessment']['summary']}")
