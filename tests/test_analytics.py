"""
Tests for Sales Analytics (analytics.py) and Data Quality (/api/data-quality).

All metric assertions are structural (keys exist, types correct, values sane) rather
than hardcoded numbers, since the exact values depend on the test fixture data.
"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from test_base import IsolatedDatabaseTestCase
import api as api_module


class TestManagerDashboard(IsolatedDatabaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = api_module.app.test_client()

    def test_endpoint_returns_200(self):
        resp = self.client.get('/api/analytics/manager')
        self.assertEqual(resp.status_code, 200)

    def test_required_keys_present(self):
        data = self.client.get('/api/analytics/manager').get_json()
        required = [
            'total_leads', 'new_leads', 'contacted_leads', 'qualified_leads',
            'interested_leads', 'site_visits', 'negotiations', 'won_deals',
            'lost_leads', 'pipeline_value_egp', 'conversion_rate_pct',
            'real_leads', 'synthetic_leads', 'data_basis',
        ]
        for key in required:
            self.assertIn(key, data, f'Missing key: {key}')

    def test_data_basis_is_labelled(self):
        data = self.client.get('/api/analytics/manager').get_json()
        self.assertIn(data['data_basis'], ('real', 'synthetic', 'mixed'))

    def test_no_hardcoded_zeros_when_leads_exist(self):
        data = self.client.get('/api/analytics/manager').get_json()
        # The isolated DB has 1 lead seeded by test_base
        self.assertGreater(data['total_leads'], 0)

    def test_conversion_rate_is_percentage(self):
        data = self.client.get('/api/analytics/manager').get_json()
        rate = data['conversion_rate_pct']
        self.assertGreaterEqual(rate, 0.0)
        self.assertLessEqual(rate, 100.0)


class TestConversionFunnel(IsolatedDatabaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = api_module.app.test_client()

    def test_endpoint_returns_200(self):
        resp = self.client.get('/api/analytics/funnel')
        self.assertEqual(resp.status_code, 200)

    def test_funnel_is_a_list(self):
        data = self.client.get('/api/analytics/funnel').get_json()
        self.assertIsInstance(data['funnel'], list)
        self.assertGreater(len(data['funnel']), 0)

    def test_funnel_stages_have_required_keys(self):
        data = self.client.get('/api/analytics/funnel').get_json()
        for stage in data['funnel']:
            for key in ('stage', 'label', 'count'):
                self.assertIn(key, stage)

    def test_end_to_end_conversion_present(self):
        data = self.client.get('/api/analytics/funnel').get_json()
        self.assertIn('end_to_end_conversion_pct', data)


class TestDataQualityEndpoint(IsolatedDatabaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = api_module.app.test_client()

    def test_endpoint_returns_200(self):
        resp = self.client.get('/api/data-quality')
        self.assertEqual(resp.status_code, 200)

    def test_properties_section_present(self):
        data = self.client.get('/api/data-quality').get_json()
        self.assertIn('properties', data)
        props = data['properties']
        for key in ('total', 'real', 'synthetic', 'missing_price',
                    'missing_location', 'duplicate_count', 'invalid_psqm',
                    'installment_extraction_rate_pct'):
            self.assertIn(key, props, f'Missing property key: {key}')

    def test_leads_section_present(self):
        data = self.client.get('/api/data-quality').get_json()
        self.assertIn('leads', data)
        for key in ('total', 'with_budget', 'budget_completeness_pct'):
            self.assertIn(key, data['leads'])

    def test_real_plus_synthetic_equals_total(self):
        data = self.client.get('/api/data-quality').get_json()
        props = data['properties']
        self.assertEqual(props['real'] + props['synthetic'], props['total'])


class TestAgentsEndpoints(IsolatedDatabaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = api_module.app.test_client()

    def test_create_agent(self):
        resp = self.client.post('/api/agents', json={'name': 'Test Agent'})
        self.assertEqual(resp.status_code, 201)
        self.assertIn('id', resp.get_json())

    def test_list_agents(self):
        self.client.post('/api/agents', json={'name': 'Agent A'})
        resp = self.client.get('/api/agents')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)

    def test_create_agent_missing_name(self):
        resp = self.client.post('/api/agents', json={})
        self.assertEqual(resp.status_code, 400)

    def test_get_agent_by_id(self):
        create_resp = self.client.post('/api/agents', json={'name': 'Named Agent'})
        agent_id = create_resp.get_json()['id']
        resp = self.client.get(f'/api/agents/{agent_id}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['name'], 'Named Agent')

    def test_get_nonexistent_agent(self):
        resp = self.client.get('/api/agents/99999')
        self.assertEqual(resp.status_code, 404)

    def test_assign_lead_to_agent(self):
        agent_id = self.client.post('/api/agents', json={'name': 'Assign Agent'}).get_json()['id']
        resp = self.client.post('/api/leads/1/assign', json={'agent_id': agent_id})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['ok'])

    def test_assign_nonexistent_lead(self):
        agent_id = self.client.post('/api/agents', json={'name': 'X Agent'}).get_json()['id']
        resp = self.client.post('/api/leads/99999/assign', json={'agent_id': agent_id})
        self.assertEqual(resp.status_code, 404)

    def test_assignment_history(self):
        agent_id = self.client.post('/api/agents', json={'name': 'Hist Agent'}).get_json()['id']
        self.client.post('/api/leads/1/assign', json={'agent_id': agent_id})
        resp = self.client.get('/api/leads/1/assignments')
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)


class TestPipelineHistoryEndpoint(IsolatedDatabaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = api_module.app.test_client()

    def test_history_endpoint_exists(self):
        resp = self.client.get('/api/leads/1/pipeline-history')
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

    def test_history_after_transition(self):
        self.client.put('/api/leads/1/status', json={'status': 'contacted'})
        resp = self.client.get('/api/leads/1/pipeline-history')
        history = resp.get_json()
        self.assertGreater(len(history), 0)

    def test_history_nonexistent_lead(self):
        resp = self.client.get('/api/leads/99999/pipeline-history')
        self.assertEqual(resp.status_code, 404)


class TestStatusTransitionEndpoint(IsolatedDatabaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = api_module.app.test_client()

    def test_valid_transition(self):
        resp = self.client.put('/api/leads/1/status', json={'status': 'contacted'})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['ok'])

    def test_invalid_status_value(self):
        resp = self.client.put('/api/leads/1/status', json={'status': 'banana'})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_transition_rejected(self):
        # Lead is at 'new'; jumping to negotiation is not allowed
        resp = self.client.put('/api/leads/1/status', json={'status': 'negotiation'})
        self.assertEqual(resp.status_code, 422)


if __name__ == '__main__':
    unittest.main(verbosity=2)

