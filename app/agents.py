"""
Sales Agent management: CRUD + lead assignment.

Agents are the human sales reps who use the copilot.
A lead can be assigned to one current agent at a time (is_current=1),
but the full assignment history is preserved in lead_assignments.
"""

import os
from db import get_connection


# ---------------------------------------------------------------------------
# Agent CRUD
# ---------------------------------------------------------------------------

def create_agent(name, email=None, phone=None, conn=None):
    """Creates a new sales agent. Returns the new agent id."""
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()
    cur = conn.execute(
        "INSERT INTO sales_agents (name, email, phone) VALUES (?, ?, ?)",
        (name, email, phone),
    )
    agent_id = cur.lastrowid
    if owns_conn:
        conn.commit()
        conn.close()
    return agent_id


def get_agent(agent_id, conn=None):
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()
    row = conn.execute("SELECT * FROM sales_agents WHERE id = ?", (agent_id,)).fetchone()
    if owns_conn:
        conn.close()
    return dict(row) if row else None


def list_agents(active_only=True, conn=None):
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()
    query = "SELECT * FROM sales_agents"
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY name"
    rows = conn.execute(query).fetchall()
    if owns_conn:
        conn.close()
    return [dict(r) for r in rows]


def deactivate_agent(agent_id, conn=None):
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()
    existing = conn.execute(
        "SELECT id FROM sales_agents WHERE id = ?", (agent_id,)
    ).fetchone()
    if not existing:
        if owns_conn:
            conn.close()
        return False
    conn.execute("UPDATE sales_agents SET is_active = 0 WHERE id = ?", (agent_id,))
    if owns_conn:
        conn.commit()
        conn.close()
    return True


# ---------------------------------------------------------------------------
# Lead assignment
# ---------------------------------------------------------------------------

def assign_lead(lead_id, agent_id, assigned_by=None, conn=None):
    """
    Assigns lead_id to agent_id.
    - Marks all previous assignments for this lead as is_current = 0.
    - Inserts a new assignment row with is_current = 1.
    - Updates leads.assigned_agent_id.
    Returns {"ok": True} or {"ok": False, "error": "..."}.
    """
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()

    lead = conn.execute("SELECT id FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        if owns_conn:
            conn.close()
        return {"ok": False, "error": f"Lead {lead_id} not found"}

    agent = conn.execute(
        "SELECT id FROM sales_agents WHERE id = ? AND is_active = 1", (agent_id,)
    ).fetchone()
    if not agent:
        if owns_conn:
            conn.close()
        return {"ok": False, "error": f"Agent {agent_id} not found or inactive"}

    # Retire previous assignment(s)
    conn.execute(
        "UPDATE lead_assignments SET is_current = 0 WHERE lead_id = ? AND is_current = 1",
        (lead_id,),
    )
    conn.execute("""
        INSERT INTO lead_assignments (lead_id, agent_id, assigned_by, is_current)
        VALUES (?, ?, ?, 1)
    """, (lead_id, agent_id, assigned_by))
    conn.execute(
        "UPDATE leads SET assigned_agent_id = ?, updated_at = datetime('now') WHERE id = ?",
        (agent_id, lead_id),
    )

    if owns_conn:
        conn.commit()
        conn.close()

    return {"ok": True, "lead_id": lead_id, "agent_id": agent_id}


def get_assignment_history(lead_id, conn=None):
    """Returns the full assignment history for a lead (newest first)."""
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()
    rows = conn.execute("""
        SELECT la.*, sa.name as agent_name
        FROM lead_assignments la
        JOIN sales_agents sa ON sa.id = la.agent_id
        WHERE la.lead_id = ?
        ORDER BY la.assigned_at DESC
    """, (lead_id,)).fetchall()
    if owns_conn:
        conn.close()
    return [dict(r) for r in rows]


def seed_default_agents(conn=None):
    """Creates default sales agents if none exist, and assigns unassigned leads.

    Demo only: when login is required (a real deployment) no made-up agents are created and no leads
    are handed out, so brokers never see placeholder staff or find their leads assigned to them."""
    if os.environ.get("TASAHEL_REQUIRE_AUTH", "0").strip().lower() in ("1", "true", "yes"):
        return
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()
    count = conn.execute("SELECT COUNT(*) c FROM sales_agents").fetchone()["c"]
    if count == 0:
        agents = [
            ("Karim El-Sayed", "karim@tasahel.com", "+201012345671"),
            ("Nour Mansour", "nour@tasahel.com", "+201012345672"),
            ("Youssef Nabil", "youssef@tasahel.com", "+201012345673"),
        ]
        created_ids = []
        for name, email, phone in agents:
            cur = conn.execute(
                "INSERT INTO sales_agents (name, email, phone) VALUES (?, ?, ?)",
                (name, email, phone),
            )
            created_ids.append(cur.lastrowid)

        # Link demo agent user account to Karim
        if created_ids:
            conn.execute(
                "UPDATE users SET agent_id = ? WHERE email = 'agent@tasahel.com'",
                (created_ids[0],),
            )

        # Distribute existing unassigned leads
        leads = [r["id"] for r in conn.execute("SELECT id FROM leads WHERE assigned_agent_id IS NULL").fetchall()]
        for idx, lid in enumerate(leads):
            ag_id = created_ids[idx % len(created_ids)]
            conn.execute("UPDATE leads SET assigned_agent_id = ? WHERE id = ?", (ag_id, lid))
            conn.execute(
                "INSERT INTO lead_assignments (lead_id, agent_id, assigned_by, is_current) VALUES (?, ?, 'system_seed', 1)",
                (lid, ag_id),
            )

        if owns_conn:
            conn.commit()
    if owns_conn:
        conn.close()
