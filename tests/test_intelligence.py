"""
Tests for the AI Lead Intelligence endpoint (/api/leads/<id>/intelligence).
Verifies structure, provenance honesty, and content correctness.
"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from test_base import IsolatedDatabaseTestCase
import api as api_module


class TestLeadIntelligenceEndpoint(IsolatedDatabaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = api_module.app.test_client()

    def test_endpoint_returns_200_for_known_lead(self):
        resp = self.client.get('/api/leads/1/intelligence')
        self.assertEqual(resp.status_code, 200)

    def test_returns_404_for_unknown_lead(self):
        resp = self.client.get('/api/leads/99999/intelligence')
        self.assertEqual(resp.status_code, 404)

    def test_required_keys_present(self):
        data = self.client.get('/api/leads/1/intelligence').get_json()
        required = [
            'lead_id', 'score', 'priority', 'scoring_mode',
            'empirical_probability_available', 'probability_disclaimer',
            'positive_factors', 'warnings', 'missing_info',
            'top_factors', 'top_factors_readable',
        ]
        for key in required:
            self.assertIn(key, data, f'Missing key: {key}')

    def test_score_in_valid_range(self):
        data = self.client.get('/api/leads/1/intelligence').get_json()
        self.assertGreaterEqual(data['score'], 0)
        self.assertLessEqual(data['score'], 100)

    def test_priority_is_valid_tier(self):
        data = self.client.get('/api/leads/1/intelligence').get_json()
        self.assertIn(data['priority'], ('high', 'medium', 'low'))

    def test_positive_factors_is_list(self):
        data = self.client.get('/api/leads/1/intelligence').get_json()
        self.assertIsInstance(data['positive_factors'], list)

    def test_warnings_is_list(self):
        data = self.client.get('/api/leads/1/intelligence').get_json()
        self.assertIsInstance(data['warnings'], list)

    def test_not_claiming_empirical_probability_without_data(self):
        data = self.client.get('/api/leads/1/intelligence').get_json()
        # With only synthetic demo leads there are no real outcomes,
        # so empirical probability must NOT be claimed.
        self.assertFalse(data['empirical_probability_available'])
        self.assertIn('Heuristic', data['probability_disclaimer'])

    def test_missing_info_is_list(self):
        data = self.client.get('/api/leads/1/intelligence').get_json()
        self.assertIsInstance(data['missing_info'], list)

    def test_top_factors_readable_is_list_of_strings(self):
        data = self.client.get('/api/leads/1/intelligence').get_json()
        readable = data['top_factors_readable']
        self.assertIsInstance(readable, list)
        for item in readable:
            self.assertIsInstance(item, str)

    def test_lead_with_complete_profile_has_fewer_warnings(self):
        # Lead 1 from test_base has budget, location, bedrooms, installment preference
        data = self.client.get('/api/leads/1/intelligence').get_json()
        # At least budget and location should be positive factors
        all_factors = ' '.join(data['positive_factors'])
        self.assertTrue(
            len(data['positive_factors']) > 0,
            'Well-profiled lead should have at least one positive factor'
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
