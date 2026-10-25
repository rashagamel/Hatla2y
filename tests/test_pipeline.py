"""
Tests for the Pipeline State Machine (pipeline.py).

Covers:
  - Valid and invalid stage transitions
  - Transition history recording
  - Lost reason validation
  - Idempotent same-status transitions
  - Legacy alias acceptance
"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from test_base import IsolatedDatabaseTestCase
from db import get_connection
from pipeline import (
    transition_lead, get_pipeline_history, is_valid_transition,
    normalize_status, PIPELINE_STAGES, ALL_VALID_STATUSES, STAGE_ALIASES,
)


class TestNormalization(unittest.TestCase):
    def test_canonical_stages_unchanged(self):
        for s in PIPELINE_STAGES:
            self.assertEqual(normalize_status(s), s)

    def test_legacy_viewing_booked(self):
        self.assertEqual(normalize_status('viewing_booked'), 'site_visit')

    def test_legacy_negotiating(self):
        self.assertEqual(normalize_status('negotiating'), 'negotiation')


class TestTransitionValidation(unittest.TestCase):
    def test_new_to_contacted_valid(self):
        self.assertTrue(is_valid_transition('new', 'contacted'))

    def test_new_to_qualified_invalid(self):
        self.assertFalse(is_valid_transition('new', 'qualified'))

    def test_new_to_lost_valid(self):
        self.assertTrue(is_valid_transition('new', 'lost'))

    def test_won_to_any_invalid(self):
        for s in PIPELINE_STAGES:
            if s != 'won':
                self.assertFalse(is_valid_transition('won', s))

    def test_lost_to_new_valid(self):
        self.assertTrue(is_valid_transition('lost', 'new'))

    def test_site_visit_to_negotiation_valid(self):
        self.assertTrue(is_valid_transition('site_visit', 'negotiation'))

    def test_legacy_alias_in_from_position(self):
        # viewing_booked is a valid from_status (legacy data)
        self.assertTrue(is_valid_transition('viewing_booked', 'negotiation'))


class TestTransitionExecution(IsolatedDatabaseTestCase):
    """Each test operates on the isolated temp DB seeded by IsolatedDatabaseTestCase."""

    def _new_lead(self, name='Pipeline Test Lead'):
        conn = get_connection()
        cur = conn.execute(
            'INSERT INTO leads (name, data_source) VALUES (?, ?)',(name, 'synthetic')
        )
        lead_id = cur.lastrowid
        conn.commit()
        conn.close()
        return lead_id

    def test_valid_transition_recorded(self):
        lid = self._new_lead()
        result = transition_lead(lid, 'contacted', actor='test_agent')
        self.assertTrue(result['ok'])
        self.assertEqual(result['from'], 'new')
        self.assertEqual(result['to'], 'contacted')
        self.assertTrue(result['changed'])

    def test_invalid_transition_rejected(self):
        lid = self._new_lead()
        result = transition_lead(lid, 'negotiation')
        self.assertFalse(result['ok'])
        self.assertIn('error', result)

    def test_unknown_status_rejected(self):
        lid = self._new_lead()
        result = transition_lead(lid, 'foobar')
        self.assertFalse(result['ok'])

    def test_idempotent_same_status(self):
        lid = self._new_lead()
        result = transition_lead(lid, 'new')
        self.assertTrue(result['ok'])
        self.assertFalse(result.get('changed', True))

    def test_full_pipeline_happy_path(self):
        lid = self._new_lead('Full Pipeline Lead')
        for stage in ('contacted', 'qualified', 'interested', 'site_visit', 'negotiation', 'won'):
            r = transition_lead(lid, stage, actor='agent')
            self.assertTrue(r['ok'], f'Failed at {stage}: {r}')

    def test_lost_reason_valid(self):
        lid = self._new_lead()
        transition_lead(lid, 'contacted')
        result = transition_lead(lid, 'lost', lost_reason='price')
        self.assertTrue(result['ok'])

    def test_lost_reason_invalid_rejected(self):
        lid = self._new_lead('Bad Lost Reason')
        transition_lead(lid, 'contacted')
        result = transition_lead(lid, 'lost', lost_reason='not_a_reason')
        self.assertFalse(result['ok'])

    def test_history_recorded(self):
        lid = self._new_lead('History Lead')
        transition_lead(lid, 'contacted', actor='agent1', reason='warm inquiry')
        transition_lead(lid, 'qualified', actor='agent1')
        history = get_pipeline_history(lid)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]['to_status'], 'contacted')
        self.assertEqual(history[0]['actor'], 'agent1')
        self.assertEqual(history[0]['reason'], 'warm inquiry')

    def test_nonexistent_lead_returns_error(self):
        result = transition_lead(99999, 'contacted')
        self.assertFalse(result['ok'])
        self.assertIn('not found', result['error'])

    def test_legacy_status_accepted(self):
        lid = self._new_lead('Legacy Lead')
        transition_lead(lid, 'contacted')
        result = transition_lead(lid, 'viewing_booked')  # legacy alias for site_visit
        self.assertTrue(result['ok'], str(result))


if __name__ == '__main__':
    unittest.main(verbosity=2)
