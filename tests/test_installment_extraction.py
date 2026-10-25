"""
Unit tests for installment term extraction and payment plan provenance.

Verifies:
  1. Arabic duration, down payment, monthly payment, and delivery extraction
  2. English duration, down payment, monthly payment, and delivery extraction
  3. Provenance labeling (extracted_from_description vs assumed_default vs structured)
  4. Mathematical accuracy of calculated monthly payments
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from installment_extractor import (
    extract_plan_duration_years,
    extract_down_payment,
    extract_monthly_payment,
    extract_delivery_year,
    extract_installment_terms,
    resolve_installment_plan,
)


class TestInstallmentExtraction(unittest.TestCase):

    def test_arabic_duration_patterns(self):
        cases = [
            ("شقة للبيع بالتقسيط على 8 سنوات بمقدم 10%", 8),
            ("فيلا للبيع مع تسهيلات حتى 10 سنين", 10),
            ("أقساط متساوية على 7 سنوات بدون فوائد", 7),
            ("تقسيط على 84 شهر بأفضل موقع", 7),
            ("5 سنوات تقسيط مباشر مع المطور", 5),
            ("ادفع وقسط على سنتين", 2),
            ("تسهيلات في السداد على ثلاث سنوات", 3),
        ]
        for text, expected in cases:
            years, snip = extract_plan_duration_years(text)
            self.assertEqual(years, expected, f"Failed for '{text}' — got {years}, expected {expected}")
            self.assertIsNotNone(snip)

    def test_english_duration_patterns(self):
        cases = [
            ("Apartment with 8 years equal installments in New Cairo", 8),
            ("Payment plan over 6 years with 10% down payment", 6),
            ("Ready to move, installments up to 7 years", 7),
            ("10 yrs payment plan, zero interest", 10),
            ("Over 60 months with equal installments", 5),
        ]
        for text, expected in cases:
            years, snip = extract_plan_duration_years(text)
            self.assertEqual(years, expected, f"Failed for '{text}' — got {years}, expected {expected}")
            self.assertIsNotNone(snip)

    def test_arabic_down_payment_extraction(self):
        pct, amt, snip = extract_down_payment("شقة مميزة بمقدم 10% والباقي أقساط", total_price=5_000_000)
        self.assertEqual(pct, 10.0)
        self.assertEqual(amt, 500_000)

        pct2, amt2, snip2 = extract_down_payment("ادفع مقدم 500 الف وقسط الباقي", total_price=5_000_000)
        self.assertEqual(amt2, 500_000)
        self.assertEqual(pct2, 10.0)

    def test_english_down_payment_extraction(self):
        pct, amt, snip = extract_down_payment("5% DP and 8 years equal installments", total_price=4_000_000)
        self.assertEqual(pct, 5.0)
        self.assertEqual(amt, 200_000)

        pct2, amt2, snip2 = extract_down_payment("Down payment of 15% required", total_price=2_000_000)
        self.assertEqual(pct2, 15.0)
        self.assertEqual(amt2, 300_000)

    def test_monthly_payment_extraction(self):
        # Arabic
        m1, s1 = extract_monthly_payment("قسط شهري 25 الف جنيه فقط")
        self.assertEqual(m1, 25_000)

        # English
        m2, s2 = extract_monthly_payment("Monthly: 30k EGP with no hidden fees")
        self.assertEqual(m2, 30_000)

    def test_delivery_year_extraction(self):
        d1, _ = extract_delivery_year("استلام 2026 تشطيب كامل")
        self.assertEqual(d1, 2026)

        d2, _ = extract_delivery_year("Handover in 2027")
        self.assertEqual(d2, 2027)

    def test_resolve_plan_extracted_provenance(self):
        desc = "شقة للبيع بمقدم 10% وأقساط على 8 سنوات"
        plan = resolve_installment_plan(desc, price=4_000_000)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["years"], 8)
        self.assertEqual(plan["down_payment_pct"], 10.0)
        self.assertEqual(plan["total_price"], 4_000_000)
        # Expected monthly: (4M - 400k) / (8 * 12) = 3.6M / 96 = 37,500
        self.assertEqual(plan["monthly_payment"], 37_500)
        self.assertEqual(plan["plan_source"], "extracted_from_description")
        self.assertIn("extracted_terms", plan)

    def test_resolve_plan_fallback_to_assumed_default(self):
        desc = "شقة رائعة بدون ذكر تفاصيل التقسيط"
        plan = resolve_installment_plan(desc, price=3_000_000, fallback_years=7)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["years"], 7)
        self.assertEqual(plan["plan_source"], "assumed_default")


if __name__ == "__main__":
    unittest.main()
