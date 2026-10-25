"""
Sales Pipeline State Machine.

Defines the valid stages of the sales lifecycle and enforces valid
transitions between them.  All status changes are recorded in the
pipeline_transitions table so that conversion analytics and funnel
visualisation are based on real transition history, not reconstructed
from a single status column snapshot.

Canonical stages (in order):
    new -> contacted -> qualified -> interested -> site_visit -> negotiation -> won / lost

Legacy aliases (kept for backward compatibility with rows already in the DB):
    viewing_booked  ->  site_visit
    negotiating     ->  negotiation
"""

from db import get_connection

# ---------------------------------------------------------------------------
# Stage vocabulary
# ---------------------------------------------------------------------------
PIPELINE_STAGES = [
    "new", "contacted", "qualified", "interested",
    "site_visit", "negotiation", "won", "lost",
]

# Legacy DB values accepted on read; normalised to canonical form for analytics.
STAGE_ALIASES = {
    "viewing_booked": "site_visit",
    "negotiating":    "negotiation",
}

# All values that the DB status column may legally contain (canonical + legacy).
ALL_VALID_STATUSES = set(PIPELINE_STAGES) | set(STAGE_ALIASES.keys())

# Allowed next stages from each canonical stage.
VALID_TRANSITIONS = {
    "new":         {"contacted", "lost"},
    "contacted":   {"qualified", "interested", "site_visit", "lost", "new"},
    "qualified":   {"interested", "site_visit", "lost", "contacted"},
    "interested":  {"site_visit", "lost", "qualified"},
    "site_visit":  {"negotiation", "interested", "lost"},
    "negotiation": {"won", "lost", "interested"},
    "won":         set(),
    "lost":        {"new"},
    # Legacy aliases - same rules as canonical equivalents
    "viewing_booked": {"negotiation", "interested", "lost"},
    "negotiating":    {"won", "lost", "interested"},
}

STAGE_LABELS = {
    "new":           "New Lead",
    "contacted":     "Contacted",
    "qualified":     "Qualified",
    "interested":    "Interested",
    "site_visit":    "Site Visit",
    "negotiation":   "Negotiation",
    "won":           "Won",
    "lost":          "Lost",
    "viewing_booked": "Site Visit",
    "negotiating":    "Negotiation",
}

STAGE_ORDER = {
    "new": 0, "contacted": 1, "qualified": 2, "interested": 3,
    "site_visit": 4, "viewing_booked": 4,
    "negotiation": 5, "negotiating": 5,
    "won": 6, "lost": 7,
}

LOST_REASONS = (
    "price", "location", "financing", "competitor",
    "timing", "unresponsive", "other",
)


def normalize_status(status):
    """Return canonical stage name for any valid status (incl. legacy aliases)."""
    return STAGE_ALIASES.get(status, status)


def is_valid_transition(from_status, to_status):
    """Check whether a transition from from_status to to_status is allowed.

    Both from_status and to_status may be legacy aliases — normalize both
    before the lookup so that e.g. 'contacted' -> 'viewing_booked' is
    accepted whenever 'contacted' -> 'site_visit' would be.
    """
    canonical_from = normalize_status(from_status)
    canonical_to = normalize_status(to_status)
    # Use canonical_from's allowed set (which is the definitive one)
    allowed_canonical = VALID_TRANSITIONS.get(canonical_from, set())
    # Also check the raw from_status in case the VALID_TRANSITIONS table
    # has an explicit entry for the legacy alias (belt-and-suspenders).
    allowed_raw = VALID_TRANSITIONS.get(from_status, set())
    allowed = allowed_canonical | allowed_raw
    return to_status in allowed or canonical_to in allowed


def transition_lead(lead_id, new_status, actor="system", reason=None,
                    notes=None, lost_reason=None, conn=None):
    """
    Attempt a pipeline transition for lead_id.

    Returns:
        {"ok": True, "from": old, "to": new_status}   on success
        {"ok": False, "error": "..."}                  on failure

    The caller is responsible for committing if they pass their own conn.
    If conn is None, a fresh connection is opened, committed, and closed.
    """
    if new_status not in ALL_VALID_STATUSES:
        return {"ok": False,
                "error": f"Unknown status '{new_status}'. Valid: {sorted(ALL_VALID_STATUSES)}"}

    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()

    lead = conn.execute("SELECT id, status FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        if owns_conn:
            conn.close()
        return {"ok": False, "error": f"Lead {lead_id} not found"}

    old_status = lead["status"]

    if normalize_status(old_status) == normalize_status(new_status):
        if owns_conn:
            conn.close()
        return {"ok": True, "from": old_status, "to": new_status, "changed": False}

    if not is_valid_transition(old_status, new_status):
        if owns_conn:
            conn.close()
        return {
            "ok": False,
            "error": (
                f"Invalid transition: {old_status!r} -> {new_status!r}. "
                f"Allowed from {old_status!r}: {sorted(VALID_TRANSITIONS.get(old_status, set()))}"
            ),
        }

    updates = {"status": new_status}
    if lost_reason and new_status == "lost":
        if lost_reason not in LOST_REASONS:
            if owns_conn:
                conn.close()
            return {"ok": False, "error": f"lost_reason must be one of {LOST_REASONS}"}
        updates["lost_reason"] = lost_reason

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE leads SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
        (*updates.values(), lead_id),
    )

    conn.execute("""
        INSERT INTO pipeline_transitions (lead_id, from_status, to_status, actor, reason, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (lead_id, old_status, new_status, actor or "system", reason, notes))

    conn.execute("""
        INSERT INTO lead_events (lead_id, event_type, payload)
        VALUES (?, ?, ?)
    """, (lead_id, new_status,
          f"Pipeline: {STAGE_LABELS.get(old_status, old_status)} -> "
          f"{STAGE_LABELS.get(new_status, new_status)}"
          + (f" | {reason}" if reason else "")))

    if owns_conn:
        conn.commit()
        conn.close()

    return {"ok": True, "from": old_status, "to": new_status, "changed": True}


def get_pipeline_history(lead_id):
    """Returns the full ordered transition history for a lead."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT from_status, to_status, actor, reason, notes, created_at
        FROM pipeline_transitions WHERE lead_id = ? ORDER BY created_at ASC
    """, (lead_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def stage_label(status):
    return STAGE_LABELS.get(status, status.replace("_", " ").title())
