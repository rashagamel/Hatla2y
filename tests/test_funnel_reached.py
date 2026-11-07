"""The funnel counts leads that REACHED each stage (from the transition history), and Pipeline Value
counts only leads that are still active (not won, not lost)."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from test_base import IsolatedDatabaseTestCase
from db import get_connection
import analytics
import pipeline


def _new_lead(conn, name, budget):
    cur = conn.execute(
        "INSERT INTO leads (name, channel, data_source, budget_max, status) VALUES (?, 'whatsapp', 'real', ?, 'new')",
        (name, budget))
    conn.commit()
    return cur.lastrowid


def _move(lead_id, *stages, lost_reason=None):
    for s in stages:
        res = pipeline.transition_lead(lead_id, s, lost_reason=lost_reason if s == "lost" else None)
        assert res["ok"], res


class TestReachedFunnelAndPipelineValue(IsolatedDatabaseTestCase):
    def setUp(self):
        conn = get_connection()
        conn.execute("DELETE FROM pipeline_transitions")
        conn.execute("DELETE FROM leads")
        conn.commit()
        self.won = _new_lead(conn, "W", 9_000_000)
        self.lost = _new_lead(conn, "L", 9_000_000)
        self.mid = _new_lead(conn, "M", 5_000_000)
        self.fresh = _new_lead(conn, "F", 1_000_000)
        conn.close()
        _move(self.won, "contacted", "site_visit", "negotiation", "won")
        _move(self.lost, "contacted", "lost", lost_reason="financing")
        _move(self.mid, "contacted", "qualified", "interested")

    def _funnel(self):
        data = analytics.get_conversion_funnel()
        return {s["stage"]: s["count"] for s in data["funnel"]}, data

    def test_funnel_counts_leads_that_reached_each_stage(self):
        counts, data = self._funnel()
        self.assertEqual(counts["new"], 4)
        self.assertEqual(counts["contacted"], 3)       # won, lost and mid all passed through Contacted
        self.assertEqual(counts["interested"], 2)      # won (went past it) and mid
        self.assertEqual(counts["negotiation"], 1)     # only won
        self.assertEqual(data["won_deals"], 1)
        self.assertEqual(data["lost_leads"], 1)

    def test_funnel_never_grows_down_the_stages(self):
        counts, _ = self._funnel()
        values = [counts[s] for s in ("new", "contacted", "qualified", "interested", "site_visit", "negotiation")]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_pipeline_value_excludes_won_and_lost(self):
        dash = analytics.get_manager_dashboard()
        self.assertEqual(dash["pipeline_value_egp"], 6_000_000)   # 5M (mid) + 1M (fresh)


if __name__ == "__main__":
    unittest.main()
