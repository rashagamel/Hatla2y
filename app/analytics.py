"""
Sales Analytics & Business Intelligence.

All numbers are calculated from stored database records.
No hardcoded metrics are returned.  Every result is clearly labelled with
whether it is based on real, synthetic, or mixed data so dashboards can
never mislead.

Public API:
  get_manager_dashboard()   -> dict  full KPI set for a manager
  get_conversion_funnel()   -> dict  stage-by-stage conversion rates
  get_agent_performance()   -> list  per-agent productivity summary
  get_data_quality_metrics()-> dict  property & lead data quality report
"""

from datetime import datetime, timedelta
from db import get_connection
from pipeline import normalize_status, PIPELINE_STAGES, STAGE_LABELS


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------

def _canonical(status):
    return normalize_status(status) if status else "unknown"


def _stage_counts(conn):
    """Returns a dict {canonical_status: count} for all leads."""
    rows = conn.execute("SELECT status, COUNT(*) c FROM leads GROUP BY status").fetchall()
    counts = {}
    for r in rows:
        cs = _canonical(r["status"])
        counts[cs] = counts.get(cs, 0) + r["c"]
    return counts


def _reached_counts(conn):
    """{stage: number of leads that reached that stage or went beyond it}.
    Uses the transition history, so a lead that was Contacted and then Lost still counts as having
    reached Contacted. Won leads count as having reached every funnel stage."""
    funnel_stages = [s for s in PIPELINE_STAGES if s not in ("won", "lost")]
    order = {s: i for i, s in enumerate(funnel_stages)}
    best = {}
    for r in conn.execute("SELECT id, status FROM leads").fetchall():
        st = _canonical(r["status"])
        best[r["id"]] = len(funnel_stages) - 1 if st == "won" else order.get(st, 0)
    for r in conn.execute("SELECT lead_id, to_status FROM pipeline_transitions").fetchall():
        st = _canonical(r["to_status"])
        if r["lead_id"] in best:
            idx = len(funnel_stages) - 1 if st == "won" else order.get(st)
            if idx is not None and idx > best[r["lead_id"]]:
                best[r["lead_id"]] = idx
    counts = {}
    for stage, idx in order.items():
        counts[stage] = sum(1 for b in best.values() if b >= idx)
    return counts


# ---------------------------------------------------------------------------
# Manager dashboard
# ---------------------------------------------------------------------------

def get_manager_dashboard(conn=None):
    """
    Returns a comprehensive set of sales KPIs calculated from the database.

    Fields marked data_basis = 'mixed' exist because the demo DB contains
    both synthetic seed leads and real agent-entered leads.  The caller
    should surface this label in any UI rather than presenting numbers as
    purely real business performance.
    """
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()

    stage_counts = _stage_counts(conn)

    total_leads = conn.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]
    real_leads  = conn.execute(
        "SELECT COUNT(*) c FROM leads WHERE data_source = 'real'"
    ).fetchone()["c"]
    synthetic_leads = total_leads - real_leads

    won_count  = stage_counts.get("won", 0)
    lost_count = stage_counts.get("lost", 0)

    pipeline_value = conn.execute(
        "SELECT COALESCE(SUM(COALESCE(budget_max, budget_min, 0)), 0) v FROM leads "
        "WHERE status NOT IN ('won', 'lost')"
    ).fetchone()["v"]
    pipeline_value_real = conn.execute(
        "SELECT COALESCE(SUM(COALESCE(budget_max, budget_min, 0)), 0) v "
        "FROM leads WHERE data_source = 'real' AND status NOT IN ('won', 'lost')"
    ).fetchone()["v"]

    # Average response time: gap between created_at and first non-system event
    resp_rows = conn.execute("""
        SELECT l.created_at, MIN(e.created_at) first_event
        FROM leads l
        JOIN lead_events e ON e.lead_id = l.id
        WHERE e.event_type = 'message_out'
        GROUP BY l.id
    """).fetchall()
    if resp_rows:
        gaps = []
        for r in resp_rows:
            try:
                t0 = datetime.fromisoformat(r["created_at"])
                t1 = datetime.fromisoformat(r["first_event"])
                gaps.append((t1 - t0).total_seconds() / 3600)
            except Exception:
                pass
        avg_response_hours = round(sum(gaps) / len(gaps), 2) if gaps else None
    else:
        avg_response_hours = None

    # Contacts made in last 7 days
    week_ago = (datetime.now() - timedelta(days=7)).isoformat(sep=" ", timespec="seconds")
    new_last_7d = conn.execute(
        "SELECT COUNT(*) c FROM leads WHERE created_at >= ?", (week_ago,)
    ).fetchone()["c"]

    # Lost reasons breakdown
    lost_reasons = {}
    for r in conn.execute(
        "SELECT lost_reason, COUNT(*) c FROM leads WHERE status = 'lost' "
        "GROUP BY lost_reason"
    ).fetchall():
        lost_reasons[r["lost_reason"] or "unspecified"] = r["c"]

    # Agent assignment coverage
    assigned_count = conn.execute(
        "SELECT COUNT(*) c FROM leads WHERE assigned_agent_id IS NOT NULL"
    ).fetchone()["c"]

    if owns_conn:
        conn.close()

    conversion_rate = round(won_count / total_leads * 100, 1) if total_leads > 0 else 0.0

    data_basis = "real" if synthetic_leads == 0 else (
        "synthetic" if real_leads == 0 else "mixed"
    )

    return {
        # Lead counts by stage
        "total_leads":       total_leads,
        "new_leads":         stage_counts.get("new", 0),
        "contacted_leads":   stage_counts.get("contacted", 0),
        "qualified_leads":   stage_counts.get("qualified", 0),
        "interested_leads":  stage_counts.get("interested", 0),
        "site_visits":       stage_counts.get("site_visit", 0),
        "negotiations":      stage_counts.get("negotiation", 0),
        "won_deals":         won_count,
        "lost_leads":        lost_count,
        # Pipeline value
        "pipeline_value_egp":      round(pipeline_value, -3),
        "pipeline_value_real_egp": round(pipeline_value_real, -3),
        # Efficiency
        "conversion_rate_pct":    conversion_rate,
        "avg_response_hours":     avg_response_hours,
        "new_leads_last_7_days":  new_last_7d,
        "assigned_leads":         assigned_count,
        "unassigned_leads":       total_leads - assigned_count,
        # Provenance
        "real_leads":             real_leads,
        "synthetic_leads":        synthetic_leads,
        "lost_reasons":           lost_reasons,
        "data_basis":             data_basis,
        "data_basis_warning": (
            "Metrics include synthetic/demo data and should not be presented "
            "as real business performance." if data_basis != "real" else None
        ),
    }


# ---------------------------------------------------------------------------
# Conversion funnel
# ---------------------------------------------------------------------------

def get_conversion_funnel(conn=None):
    """
    Returns stage-by-stage counts and conversion rates.
    The funnel follows the canonical stage order.  Lost leads are counted
    separately as an attrition number, not as a funnel stage exit.
    """
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()

    stage_counts = _stage_counts(conn)
    reached = _reached_counts(conn)
    if owns_conn:
        conn.close()

    ordered_stages = [s for s in PIPELINE_STAGES if s not in ("won", "lost")]
    funnel = []
    prev_count = None
    for stage in ordered_stages:
        count = reached.get(stage, 0)
        rate = round(count / prev_count * 100, 1) if prev_count and prev_count > 0 else None
        funnel.append({
            "stage":          stage,
            "label":          STAGE_LABELS.get(stage, stage),
            "count":          count,                 # leads that reached this stage or went past it
            "current_count":  stage_counts.get(stage, 0),
            "conversion_rate_pct": rate,   # rate FROM the previous stage
        })
        prev_count = count

    won  = stage_counts.get("won", 0)
    lost = stage_counts.get("lost", 0)
    total = stage_counts.get("new", 0) + sum(
        stage_counts.get(s, 0) for s in PIPELINE_STAGES if s != "new"
    )
    end_to_end = round(won / total * 100, 1) if total > 0 else 0.0

    return {
        "funnel":                     funnel,
        "won_deals":                  won,
        "lost_leads":                 lost,
        "end_to_end_conversion_pct":  end_to_end,
    }


# ---------------------------------------------------------------------------
# Agent performance
# ---------------------------------------------------------------------------

def get_agent_performance(agent_id=None, conn=None):
    """
    Returns per-agent productivity metrics.
    If agent_id is given, returns a single-item list for that agent.
    """
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()

    if agent_id:
        agents = conn.execute(
            "SELECT * FROM sales_agents WHERE id = ? AND is_active = 1", (agent_id,)
        ).fetchall()
    else:
        agents = conn.execute(
            "SELECT * FROM sales_agents WHERE is_active = 1 ORDER BY name"
        ).fetchall()

    results = []
    for agent in agents:
        aid = agent["id"]
        total = conn.execute(
            "SELECT COUNT(*) c FROM leads WHERE assigned_agent_id = ?", (aid,)
        ).fetchone()["c"]
        won = conn.execute(
            "SELECT COUNT(*) c FROM leads WHERE assigned_agent_id = ? AND status = 'won'", (aid,)
        ).fetchone()["c"]
        lost = conn.execute(
            "SELECT COUNT(*) c FROM leads WHERE assigned_agent_id = ? AND status = 'lost'", (aid,)
        ).fetchone()["c"]
        active = total - won - lost

        results.append({
            "agent_id":      aid,
            "agent_name":    agent["name"],
            "total_leads":   total,
            "active_leads":  active,
            "won_deals":     won,
            "lost_leads":    lost,
            "win_rate_pct":  round(won / total * 100, 1) if total > 0 else 0.0,
        })

    if owns_conn:
        conn.close()

    return results


# ---------------------------------------------------------------------------
# Data quality metrics
# ---------------------------------------------------------------------------

def get_data_quality_metrics(conn=None):
    """
    Returns a property & lead data quality report.
    All numbers are calculated from the database — nothing is hardcoded.
    """
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()

    total_props = conn.execute("SELECT COUNT(*) c FROM properties").fetchone()["c"]
    real_props  = conn.execute(
        "SELECT COUNT(*) c FROM properties WHERE data_source != 'synthetic'"
    ).fetchone()["c"]
    synth_props = total_props - real_props

    missing_price = conn.execute(
        "SELECT COUNT(*) c FROM properties WHERE cash_price IS NULL OR cash_price <= 0"
    ).fetchone()["c"]
    missing_location = conn.execute(
        "SELECT COUNT(*) c FROM properties WHERE location IS NULL OR location = ''"
    ).fetchone()["c"]
    missing_area = conn.execute(
        "SELECT COUNT(*) c FROM properties WHERE area_sqm IS NULL OR area_sqm <= 0"
    ).fetchone()["c"]

    # Duplicate detection: same title + location + area_sqm + price
    dup_count = conn.execute("""
        SELECT COUNT(*) - COUNT(DISTINCT title || '|' || location || '|' ||
               CAST(area_sqm AS TEXT) || '|' || CAST(cash_price AS TEXT)) c
        FROM properties
    """).fetchone()["c"]

    # Installment extraction rate
    total_plans = conn.execute("SELECT COUNT(*) c FROM installment_plans").fetchone()["c"]
    extracted_plans = conn.execute(
        "SELECT COUNT(*) c FROM installment_plans "
        "WHERE plan_source IN ('extracted_from_description','extracted_duration','extracted_down_payment')"
    ).fetchone()["c"]
    extraction_rate = round(extracted_plans / total_plans * 100, 1) if total_plans > 0 else 0.0

    # Invalid records: price-per-sqm outside 5k-400k EGP/sqm
    invalid_psqm = conn.execute("""
        SELECT COUNT(*) c FROM properties
        WHERE area_sqm > 0 AND cash_price > 0
          AND (cash_price / area_sqm < 5000 OR cash_price / area_sqm > 400000)
    """).fetchone()["c"]

    # Source distribution
    source_rows = conn.execute(
        "SELECT data_source, COUNT(*) c FROM properties GROUP BY data_source"
    ).fetchall()
    source_dist = {r["data_source"]: r["c"] for r in source_rows}

    # Lead data completeness
    total_leads = conn.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]
    leads_with_budget = conn.execute(
        "SELECT COUNT(*) c FROM leads WHERE budget_max IS NOT NULL AND budget_max > 0"
    ).fetchone()["c"]
    leads_with_location = conn.execute(
        "SELECT COUNT(*) c FROM leads WHERE preferred_locations IS NOT NULL "
        "AND preferred_locations != ''"
    ).fetchone()["c"]
    leads_with_bedrooms = conn.execute(
        "SELECT COUNT(*) c FROM leads WHERE min_bedrooms IS NOT NULL"
    ).fetchone()["c"]

    if owns_conn:
        conn.close()

    return {
        "properties": {
            "total":            total_props,
            "real":             real_props,
            "synthetic":        synth_props,
            "missing_price":    missing_price,
            "missing_location": missing_location,
            "missing_area":     missing_area,
            "duplicate_count":  dup_count,
            "invalid_psqm":     invalid_psqm,
            "total_plans":      total_plans,
            "extracted_plans":  extracted_plans,
            "installment_extraction_rate_pct": extraction_rate,
            "source_distribution": source_dist,
        },
        "leads": {
            "total":                  total_leads,
            "with_budget":            leads_with_budget,
            "with_location":          leads_with_location,
            "with_bedrooms":          leads_with_bedrooms,
            "budget_completeness_pct": round(leads_with_budget / total_leads * 100, 1)
                                       if total_leads > 0 else 0.0,
        },
    }
