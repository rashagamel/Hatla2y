"""
Tests for Arabic conversation extraction — the real gap this project's own
evaluation had flagged (see README/CHANGELOG): the heuristic extractor was
essentially blind on Arabic-script text. This adds genuine regex-based
Arabic parsing for the core fields and tests it against both the exact
user-reported example and the real labeled Arabic conversations already in
data/synthetic/conversations_labeled.json.

Run:
    cd app
    python3 -m unittest tests.test_arabic_extraction -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from extraction import extract_profile  # noqa: E402


class TestUserReportedExample(unittest.TestCase):
    """The exact message the user provided when asking for Arabic support."""

    def test_exact_user_example(self):
        msg = "انا عايز شقه ف القاهره الجديده مساحه 150 متر عدد غرف 3 وبمقدم 500 الف"
        result = extract_profile(msg)
        self.assertIn("New Cairo", result["preferred_locations"])
        self.assertEqual(result["min_bedrooms"], 3)
        self.assertEqual(result["max_down_payment"], 500_000)

    def test_down_payment_not_double_counted_as_budget(self):
        # Regression test for the exact bug found: a message with ONLY a down
        # payment stated (no budget mentioned at all) was incorrectly also
        # setting budget_max to that same figure.
        msg = "عايز شقة ف التجمع وبمقدم 500 الف"
        result = extract_profile(msg)
        self.assertIsNone(result["budget_max"])
        self.assertEqual(result["max_down_payment"], 500_000)

    def test_user_typo_and_urgency_message(self):
        msg = "انا عايز شقه ف القاهره الجديده 3 عرف وبمقدم 500 الف ومحتاجها بسرعة"
        result = extract_profile(msg)
        self.assertIn("New Cairo", result["preferred_locations"])
        self.assertEqual(result["min_bedrooms"], 3)
        self.assertEqual(result["max_down_payment"], 500_000)
        self.assertEqual(result["purchase_timeline"], "immediate")
        self.assertEqual(result["urgency"], "high")
        self.assertEqual(result["payment_preference"], "installment")


class TestArabicLocations(unittest.TestCase):

    def test_new_cairo_variants(self):
        for phrase in ["التجمع الخامس", "التجمع", "القاهره الجديده", "القاهرة الجديدة"]:
            result = extract_profile(f"عايز شقة في {phrase}")
            self.assertIn("New Cairo", result["preferred_locations"], f"failed on: {phrase}")

    def test_sheikh_zayed(self):
        result = extract_profile("عايز فيلا في الشيخ زايد")
        self.assertIn("Sheikh Zayed", result["preferred_locations"])

    def test_north_coast(self):
        result = extract_profile("عايز شاليه في الساحل الشمالي")
        self.assertIn("North Coast", result["preferred_locations"])

    def test_october(self):
        result = extract_profile("عايز شقة في أكتوبر")
        self.assertIn("6th of October", result["preferred_locations"])

    def test_other_locations(self):
        cases = [("مدينتي", "Madinaty"), ("مصر الجديدة", "Heliopolis"),
                  ("المعادي", "Maadi"), ("الزمالك", "Zamalek"), ("مدينة نصر", "Nasr City")]
        for phrase, expected in cases:
            result = extract_profile(f"عايز شقة في {phrase}")
            self.assertIn(expected, result["preferred_locations"], f"failed on: {phrase}")


class TestArabicNumbers(unittest.TestCase):

    def test_budget_in_millions(self):
        result = extract_profile("الميزانية 5 مليون")
        self.assertEqual(result["budget_max"], 5_000_000)

    def test_budget_approximate_marked_inferred(self):
        result = extract_profile("ميزانيتي حوالي 5 مليون")
        self.assertEqual(result["budget_max"], 5_000_000)
        self.assertEqual(result["field_confidence"].get("budget_max"), "inferred")

    def test_bedrooms_number_before_noun(self):
        result = extract_profile("عايز شقة 3 غرف")
        self.assertEqual(result["min_bedrooms"], 3)

    def test_bedrooms_noun_before_number(self):
        # The exact order in the user's own message: "عدد غرف 3"
        result = extract_profile("عدد غرف 3")
        self.assertEqual(result["min_bedrooms"], 3)

    def test_down_payment_thousands(self):
        result = extract_profile("مقدم 500 الف")
        self.assertEqual(result["max_down_payment"], 500_000)

    def test_down_payment_approximate(self):
        result = extract_profile("مقدم حوالي 300 الف")
        self.assertEqual(result["max_down_payment"], 300_000)

    def test_monthly_payment(self):
        result = extract_profile("قسط شهري لغاية 40 الف")
        self.assertEqual(result["max_monthly_payment"], 40_000)

    def test_arabic_indic_digits_normalized(self):
        # Some keyboards produce Arabic-Indic digits (٠١٢٣...) instead of
        # Western ones — must be normalized before any numeric regex runs.
        result = extract_profile("عايز شقة ٣ غرف بمقدم ٥٠٠ الف")
        self.assertEqual(result["min_bedrooms"], 3)
        self.assertEqual(result["max_down_payment"], 500_000)


class TestArabicPaymentPreference(unittest.TestCase):

    def test_installment_keywords(self):
        for word in ["قسط", "أقساط", "بالتقسيط", "تقسيط"]:
            result = extract_profile(f"عايز أدفع {word}")
            self.assertEqual(result["payment_preference"], "installment", f"failed on: {word}")

    def test_cash_keywords(self):
        for word in ["كاش", "نقدا"]:
            result = extract_profile(f"هدفع {word}")
            self.assertEqual(result["payment_preference"], "cash", f"failed on: {word}")


class TestArabicPurposeAndTimeline(unittest.TestCase):

    def test_family_purpose(self):
        result = extract_profile("دي لسكني أنا وعيلتي")
        self.assertEqual(result["purpose"], "family")

    def test_investment_purpose(self):
        result = extract_profile("عايزها للاستثمار")
        self.assertEqual(result["purpose"], "investment")

    def test_immediate_timeline(self):
        result = extract_profile("محتاجها بسرعة")
        self.assertEqual(result["purchase_timeline"], "immediate")

    def test_long_timeline(self):
        result = extract_profile("لسه بدور، مش مستعجل")
        self.assertEqual(result["purchase_timeline"], "6m+")


class TestRealLabeledArabicDataset(unittest.TestCase):
    """Runs against the actual 40 labeled Arabic conversations in the
    project's evaluation dataset — not hand-picked examples."""

    @classmethod
    def setUpClass(cls):
        import json
        path = os.path.join(os.path.dirname(__file__), "..", "data", "synthetic", "conversations_labeled.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        cls.arabic_conversations = [c for c in data if c["language"] == "arabic"]

    def test_dataset_has_arabic_conversations(self):
        self.assertEqual(len(self.arabic_conversations), 40)

    def test_location_recall_on_real_arabic_dataset_is_strong(self):
        # Honest measurement, not a hand-picked example — most real labeled
        # Arabic conversations that state a location should now have it
        # correctly extracted (was 0% before this session — completely blind).
        with_location_label = [c for c in self.arabic_conversations if c["labels"].get("preferred_locations")]
        correct = 0
        for c in with_location_label:
            text = " ".join(c["messages"])
            result = extract_profile(text)
            true_locs = set(c["labels"]["preferred_locations"])
            if true_locs & set(result["preferred_locations"]):
                correct += 1
        recall = correct / len(with_location_label) if with_location_label else 0
        self.assertGreater(recall, 0.8, f"location recall on real Arabic data only {recall:.2f}")

    def test_bedroom_recall_on_real_arabic_dataset_is_strong(self):
        with_beds_label = [c for c in self.arabic_conversations if c["labels"].get("min_bedrooms") is not None]
        correct = 0
        for c in with_beds_label:
            text = " ".join(c["messages"])
            result = extract_profile(text)
            if result["min_bedrooms"] == c["labels"]["min_bedrooms"]:
                correct += 1
        recall = correct / len(with_beds_label) if with_beds_label else 0
        self.assertGreater(recall, 0.8, f"bedroom recall on real Arabic data only {recall:.2f}")


class TestNoRegressionOnEnglish(unittest.TestCase):
    """Confirm adding Arabic support didn't touch English-language behavior."""

    def test_english_extraction_unchanged(self):
        result = extract_profile("Looking for a 3 bedroom apartment in New Cairo, budget 5 million, installments")
        self.assertIn("New Cairo", result["preferred_locations"])
        self.assertEqual(result["min_bedrooms"], 3)
        self.assertEqual(result["payment_preference"], "installment")


if __name__ == "__main__":
    unittest.main(verbosity=2)
