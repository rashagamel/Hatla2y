"""
Phase 5 tests — AI Lead Intelligence structured output.

Verifies that get_lead_intelligence() and /api/leads/<id>/intelligence return
all required structured dimension fields:
  score, priority, explanation,
  budget_fit, location_fit, financing_fit,
  inventory_availability, engagement, urgency,
  requirement_completeness, positive_factors, warnings, missing_info

Rules:
- All assertions are structural (key exists, type correct, value sane).
- Never checks for hardcoded numeric scores (depends on model randomness).
- No test weakens or deletes existing behavior.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from test_base import IsolatedDatabaseTestCase
import api as api_module


class TestLeadIntelligencePhase5(IsolatedDatabaseTestCase):
    """Tests for the Phase 5 structured intelligence endpoint."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = api_module.app.test_client()

    def _get_intelligence(self, lead_id=1):
        resp = self.client.get(f'/api/leads/{lead_id}/intelligence')
        self.assertEqual(resp.status_code, 200, resp.data)
        return resp.get_json()

    # ------------------------------------------------------------------
    # Required keys
    # ------------------------------------------------------------------
    def test_all_phase5_keys_present(self):
        """Spec requires these exact keys in the response."""
        data = self._get_intelligence()
        required_keys = [
            'score', 'priority', 'explanation',
            'positive_factors', 'warnings', 'missing_info',
            'budget_fit', 'location_fit', 'financing_fit',
            'inventory_availability', 'engagement', 'urgency',
            'requirement_completeness',
        ]
        for key in required_keys:
            self.assertIn(key, data, f'Missing required Phase 5 key: {key}')

    def test_backward_compat_keys_still_present(self):
        """Existing keys must not be removed — additive only."""
        data = self._get_intelligence()
        existing_keys = [
            'lead_id', 'lead_name', 'scoring_mode',
            'empirical_probability_available', 'probability_disclaimer',
            'top_factors', 'top_factors_readable',
            'match_count', 'best_match_score',
        ]
        for key in existing_keys:
            self.assertIn(key, data, f'Backward-compat key removed: {key}')

    # ------------------------------------------------------------------
    # Score + priority
    # ------------------------------------------------------------------
    def test_score_is_number_in_valid_range(self):
        data = self._get_intelligence()
        self.assertIsInstance(data['score'], (int, float))
        self.assertGreaterEqual(data['score'], 0)
        self.assertLessEqual(data['score'], 100)

    def test_priority_is_valid_tier(self):
        data = self._get_intelligence()
        self.assertIn(data['priority'], ('high', 'medium', 'low'))

    # ------------------------------------------------------------------
    # Explanation
    # ------------------------------------------------------------------
    def test_explanation_is_nonempty_string(self):
        data = self._get_intelligence()
        self.assertIsInstance(data['explanation'], str)
        self.assertGreater(len(data['explanation']), 10)

    def test_explanation_contains_lead_name_or_fallback(self):
        data = self._get_intelligence()
        # Either the lead name or "This lead" should appear
        self.assertTrue(
            'Ahmed Hassan' in data['explanation'] or 'lead' in data['explanation'].lower(),
            f"Unexpected explanation: {data['explanation']}"
        )

    # ------------------------------------------------------------------
    # Boolean dimension fields
    # ------------------------------------------------------------------
    def test_budget_fit_is_boolean(self):
        data = self._get_intelligence()
        self.assertIsInstance(data['budget_fit'], bool)

    def test_budget_fit_reason_is_string(self):
        data = self._get_intelligence()
        self.assertIsInstance(data['budget_fit_reason'], str)
        self.assertGreater(len(data['budget_fit_reason']), 0)

    def test_location_fit_is_boolean(self):
        data = self._get_intelligence()
        self.assertIsInstance(data['location_fit'], bool)

    def test_location_fit_reason_is_string(self):
        data = self._get_intelligence()
        self.assertIsInstance(data['location_fit_reason'], str)

    def test_financing_fit_is_boolean(self):
        data = self._get_intelligence()
        self.assertIsInstance(data['financing_fit'], bool)

    def test_financing_fit_reason_is_string(self):
        data = self._get_intelligence()
        self.assertIsInstance(data['financing_fit_reason'], str)

    # ------------------------------------------------------------------
    # Structured sub-objects
    # ------------------------------------------------------------------
    def test_inventory_availability_has_required_keys(self):
        data = self._get_intelligence()
        inv = data['inventory_availability']
        self.assertIn('total_matches', inv)
        self.assertIn('by_location', inv)
        self.assertIsInstance(inv['total_matches'], int)
        self.assertIsInstance(inv['by_location'], dict)

    def test_engagement_has_required_keys(self):
        data = self._get_intelligence()
        eng = data['engagement']
        for key in ('label', 'score', 'events', 'property_views', 'viewing_requests'):
            self.assertIn(key, eng, f'Missing engagement key: {key}')

    def test_engagement_label_is_valid(self):
        data = self._get_intelligence()
        self.assertIn(data['engagement']['label'], ('high', 'medium', 'low', 'very_low'))

    def test_engagement_score_is_float_in_range(self):
        data = self._get_intelligence()
        s = data['engagement']['score']
        self.assertIsInstance(s, (int, float))
        self.assertGreaterEqual(s, 0.0)
        self.assertLessEqual(s, 1.0)

    def test_urgency_has_required_keys(self):
        data = self._get_intelligence()
        urg = data['urgency']
        self.assertIn('label', urg)
        self.assertIn('source', urg)

    def test_urgency_label_is_valid(self):
        data = self._get_intelligence()
        self.assertIn(data['urgency']['label'], ('high', 'medium', 'low', 'unknown'))

    def test_requirement_completeness_has_required_keys(self):
        data = self._get_intelligence()
        rc = data['requirement_completeness']
        for key in ('ratio', 'filled', 'total'):
            self.assertIn(key, rc, f'Missing completeness key: {key}')

    def test_requirement_completeness_ratio_in_range(self):
        data = self._get_intelligence()
        ratio = data['requirement_completeness']['ratio']
        self.assertGreaterEqual(ratio, 0.0)
        self.assertLessEqual(ratio, 1.0)

    def test_requirement_completeness_filled_lte_total(self):
        data = self._get_intelligence()
        rc = data['requirement_completeness']
        self.assertLessEqual(rc['filled'], rc['total'])

    # ------------------------------------------------------------------
    # Lists
    # ------------------------------------------------------------------
    def test_positive_factors_is_list_of_strings(self):
        data = self._get_intelligence()
        self.assertIsInstance(data['positive_factors'], list)
        for item in data['positive_factors']:
            self.assertIsInstance(item, str)

    def test_warnings_is_list_of_strings(self):
        data = self._get_intelligence()
        self.assertIsInstance(data['warnings'], list)
        for item in data['warnings']:
            self.assertIsInstance(item, str)

    def test_missing_info_is_list(self):
        data = self._get_intelligence()
        self.assertIsInstance(data['missing_info'], list)

    # ------------------------------------------------------------------
    # Honesty: no empirical probability claimed without real outcomes
    # ------------------------------------------------------------------
    def test_not_claiming_empirical_probability_without_real_outcomes(self):
        data = self._get_intelligence()
        self.assertFalse(data['empirical_probability_available'])
        self.assertIn('Heuristic', data['probability_disclaimer'])

    # ------------------------------------------------------------------
    # Lead 1 specific: has complete profile — should have positive factors
    # ------------------------------------------------------------------
    def test_well_profiled_lead_has_positive_factors(self):
        """Lead 1 from test_base has budget, location, bedrooms, installment preference."""
        data = self._get_intelligence(lead_id=1)
        self.assertGreater(len(data['positive_factors']), 0,
                          'Well-profiled lead should have at least one positive factor')

    def test_lead1_location_fit_true_for_new_cairo(self):
        """Lead 1 wants New Cairo — test fixtures include New Cairo properties."""
        data = self._get_intelligence(lead_id=1)
        # New Cairo has available properties in test fixtures
        self.assertTrue(data['location_fit'],
                        f"Expected location_fit=True for New Cairo lead. Reason: {data['location_fit_reason']}")

    def test_lead1_financing_fit_assessed(self):
        """Lead 1 is an installment buyer with constraints — must get a financing_fit decision."""
        data = self._get_intelligence(lead_id=1)
        # financing_fit_reason must be something meaningful, not just the default
        self.assertNotIn('No financing constraints', data['financing_fit_reason'])

    # ------------------------------------------------------------------
    # 404 for unknown lead
    # ------------------------------------------------------------------
    def test_returns_404_for_unknown_lead(self):
        resp = self.client.get('/api/leads/99999/intelligence')
        self.assertEqual(resp.status_code, 404)


if __name__ == '__main__':
    unittest.main(verbosity=2)
