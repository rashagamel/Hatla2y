"""
Unit tests for Automated Valuation Model (AVM) for Egyptian real estate.

Verifies:
  1. Feature extraction and validation
  2. Model training and metric computation (R2, MAPE, MAE)
  3. Valuation inference (estimated price, confidence band, price per sqm)
  4. Valuation assessment vs asking price (undervalued, fair_value, overpriced)
  5. Monotonicity: larger area produces higher estimated valuation
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from valuation import (
    extract_property_features,
    estimate_property_value,
    get_valuation_metrics,
    mean_absolute_percentage_error,
)


class TestValuationModel(unittest.TestCase):

    def test_extract_property_features_defaults(self):
        row = {"location": "New Cairo", "area_sqm": 150}
        feats = extract_property_features(row)
        self.assertEqual(feats["location"], "New Cairo")
        self.assertEqual(feats["area_sqm"], 150.0)
        self.assertEqual(feats["property_type"], "apartment")
        self.assertEqual(feats["finishing"], "semi-finished")
        self.assertEqual(feats["has_pool"], 0.0)

    def test_mean_absolute_percentage_error(self):
        y_true = [100, 200]
        y_pred = [110, 180]  # 10% and 10%
        mape = mean_absolute_percentage_error(y_true, y_pred)
        self.assertAlmostEqual(mape, 10.0, places=1)

    def test_estimate_property_value_output_structure(self):
        prop = {
            "title": "Test 3BR in New Cairo",
            "location": "New Cairo",
            "property_type": "apartment",
            "area_sqm": 160,
            "bedrooms": 3,
            "bathrooms": 2,
            "finishing": "fully-finished",
            "has_pool": 0,
            "has_garden": 0,
            "asking_price": 9_000_000,
        }
        res = estimate_property_value(prop)
        self.assertIn("estimated_price", res)
        self.assertIn("estimated_price_per_sqm", res)
        self.assertIn("price_range_low", res)
        self.assertIn("price_range_high", res)
        self.assertIn("valuation_assessment", res)
        self.assertIn("price_drivers", res)

        self.assertGreater(res["estimated_price"], 1_000_000)
        self.assertLess(res["price_range_low"], res["estimated_price"])
        self.assertGreater(res["price_range_high"], res["estimated_price"])

    def test_valuation_assessment_labels(self):
        base_prop = {
            "location": "New Cairo",
            "property_type": "apartment",
            "area_sqm": 150,
            "bedrooms": 3,
            "bathrooms": 2,
            "finishing": "semi-finished",
        }
        # First get fair value estimate
        val = estimate_property_value(base_prop)
        est = val["estimated_price"]

        # 1. Undervalued case (asking 30% below estimate)
        undervalued_prop = dict(base_prop, asking_price=est * 0.7)
        res_under = estimate_property_value(undervalued_prop)
        self.assertEqual(res_under["valuation_assessment"]["verdict"], "undervalued")

        # 2. Overpriced case (asking 30% above estimate)
        overpriced_prop = dict(base_prop, asking_price=est * 1.3)
        res_over = estimate_property_value(overpriced_prop)
        self.assertEqual(res_over["valuation_assessment"]["verdict"], "overpriced")

        # 3. Fair value case (asking within 2% of estimate)
        fair_prop = dict(base_prop, asking_price=est * 1.01)
        res_fair = estimate_property_value(fair_prop)
        self.assertEqual(res_fair["valuation_assessment"]["verdict"], "fair_value")

    def test_valuation_monotonicity_with_area(self):
        small = estimate_property_value({
            "location": "New Cairo", "property_type": "apartment", "area_sqm": 100,
            "bedrooms": 2, "bathrooms": 1, "finishing": "fully-finished"
        })
        large = estimate_property_value({
            "location": "New Cairo", "property_type": "apartment", "area_sqm": 220,
            "bedrooms": 3, "bathrooms": 2, "finishing": "fully-finished"
        })
        self.assertGreater(large["estimated_price"], small["estimated_price"],
                           "Larger property must receive higher estimated valuation")

    def test_model_metrics_available(self):
        metrics = get_valuation_metrics()
        self.assertIn("r2_score", metrics)
        self.assertIn("mape_percent", metrics)
        self.assertIn("sample_size", metrics)
        self.assertGreater(metrics["sample_size"], 20)


if __name__ == "__main__":
    unittest.main()
