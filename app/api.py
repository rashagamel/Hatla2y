"""
Flask API for the Real Estate AI Sales Copilot — Phase 1.

Endpoints:
  GET  /api/properties              list properties
  GET  /api/leads                   list leads
  GET  /api/leads/<id>               single lead + its recent events
  GET  /api/leads/<id>/matches       top matches for a lead (installment-aware)
  POST /api/leads                   create a new lead
  POST /api/leads/<id>/events        log a new event (e.g. incoming WhatsApp message)
"""
from flask import Flask, g, jsonify, request, send_from_directory
from db import get_connection, init_db, migrate_schema, DB_PATH
from matching import match_lead_to_properties
from extraction import extract_profile, merge_profile_into_lead, compute_missing_fields, suggest_next_question
from conversation import generate_conversation_reply
from scoring import score_lead, humanize_factors, get_lead_intelligence
from next_action import generate_next_action, get_priority_queue, track_next_action_event
from feedback import log_outcome, pipeline_summary, retrain_from_outcomes, get_model_maturity
from affordability import compare_property_plans, get_affordability_profile
from valuation import estimate_property_value, get_valuation_metrics
from pipeline import (transition_lead, get_pipeline_history, ALL_VALID_STATUSES,
                      VALID_TRANSITIONS, LOST_REASONS, STAGE_LABELS)
from analytics import get_manager_dashboard, get_conversion_funnel, get_agent_performance, get_data_quality_metrics
from agents import (create_agent, get_agent, list_agents, deactivate_agent,
                    assign_lead, get_assignment_history, seed_default_agents)
from auth import (
    require_auth, require_manager,
    authenticate_user, create_user, list_users, update_user, change_password,
    seed_default_users,
)
import hashlib
import hmac
import json
import os
import time

app = Flask(__name__, static_folder=os.path.join(os.path.dirname(__file__), "..", "static"))


# ---------------------------------------------------------------------------
# Access control
#
#   TASAHEL_REQUIRE_AUTH=1   every /api/ route needs a valid login token, except the login endpoint
#                            and the WhatsApp webhook (which verifies Meta's signature instead).
#                            SET THIS WHEN DEPLOYED.
#   unset / 0                local demo mode: the dashboard works as "Guest" without logging in, as
#                            before. A token, if one is sent, is still validated and role rules apply.
#
# Roles: "manager"/"admin" see and do everything. "agent" sees only leads assigned to them or not yet
# assigned to anyone, and cannot delete leads, reassign them, manage agents, retrain the model, or
# open the portfolio-wide pipeline / analytics views.
# ---------------------------------------------------------------------------
_MANAGER_ROLES = ("manager", "admin")
_PUBLIC_ENDPOINTS = {"auth_login", "whatsapp_webhook", "whatsapp_verify"}
_MANAGER_ONLY_ENDPOINTS = {
    "get_users", "create_new_user", "update_existing_user",
    "retrain", "get_pipeline", "manager_dashboard", "conversion_funnel", "agents_performance",
    "data_quality", "create_new_agent", "deactivate_single_agent", "assign_lead_to_agent", "delete_lead",
}


def _auth_required():
    return os.environ.get("TASAHEL_REQUIRE_AUTH", "0").strip().lower() in ("1", "true", "yes")


def _can_access_lead(user, lead_id):
    """Managers: any lead. Agents: leads assigned to them or unassigned. An unknown id returns True
    so the route itself answers 404."""
    if user is None or user["role"] in _MANAGER_ROLES:
        return True
    conn = get_connection()
    row = conn.execute("SELECT assigned_agent_id FROM leads WHERE id = ?", (lead_id,)).fetchone()
    conn.close()
    if row is None:
        return True
    return row["assigned_agent_id"] is None or row["assigned_agent_id"] == user["agent_id"]


def _accessible_lead_ids(user):
    """None means 'every lead' (guest/demo mode, manager, admin)."""
    if user is None or user["role"] in _MANAGER_ROLES:
        return None
    conn = get_connection()
    rows = conn.execute("SELECT id FROM leads WHERE assigned_agent_id IS NULL OR assigned_agent_id = ?",
                        (user["agent_id"],)).fetchall()
    conn.close()
    return {r["id"] for r in rows}


@app.before_request
def enforce_access():
    g.user = None
    if not request.path.startswith("/api/") or request.method == "OPTIONS":
        return None
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None

    if _auth_required() or request.headers.get("Authorization"):
        user, err = require_auth(request)
        if err:
            return err
        g.user = user
    user = g.user
    if user is None:
        return None                      # guest/demo mode (only possible when auth is not required)

    is_manager = user["role"] in _MANAGER_ROLES
    if request.endpoint in _MANAGER_ONLY_ENDPOINTS and not is_manager:
        return jsonify({"error": "manager access required"}), 403
    lead_id = (request.view_args or {}).get("lead_id")
    if lead_id is not None and not _can_access_lead(user, lead_id):
        return jsonify({"error": "not found"}), 404     # 404, not 403: do not reveal that it exists
    return None


# Login brute-force guard: after 10 failed attempts for one email, block that email for 5 minutes.
# In-memory, so per process (with several gunicorn workers the limit is per worker).
_LOGIN_FAILURES = {}
_LOGIN_MAX_FAILURES = 10
_LOGIN_WINDOW_SECONDS = 300


def _login_recent_failures(key):
    cutoff = time.time() - _LOGIN_WINDOW_SECONDS
    recent = [t for t in _LOGIN_FAILURES.get(key, []) if t > cutoff]
    if recent:
        _LOGIN_FAILURES[key] = recent
    else:
        _LOGIN_FAILURES.pop(key, None)
    return recent


def _verify_meta_signature(raw_body):
    """Meta signs every webhook POST: X-Hub-Signature-256 = 'sha256=' + HMAC-SHA256(app secret, body)."""
    secret = os.environ.get("WHATSAPP_APP_SECRET", "")
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not secret or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature[len("sha256="):], expected)


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/login")
def login_page():
    return send_from_directory(app.static_folder, "login.html")


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(force=True)
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    if not email or not password:
        return jsonify({"error": "email and password are required"}), 400
    key = email.lower()
    if len(_login_recent_failures(key)) >= _LOGIN_MAX_FAILURES:
        return jsonify({"error": "too many failed attempts, try again in a few minutes"}), 429
    try:
        result = authenticate_user(email, password)
    except ValueError as e:
        _LOGIN_FAILURES.setdefault(key, []).append(time.time())
        return jsonify({"error": str(e)}), 401
    _LOGIN_FAILURES.pop(key, None)
    return jsonify(result)


@app.route("/api/auth/me")
def auth_me():
    user, err = require_auth(request)
    if err:
        return err
    return jsonify({"id": user["id"], "email": user["email"], "name": user["name"],
                    "role": user["role"], "agent_id": user["agent_id"]})


@app.route("/api/auth/change-password", methods=["POST"])
def auth_change_password():
    user, err = require_auth(request)
    if err:
        return err
    data = request.get_json(force=True)
    try:
        change_password(user["id"], data.get("current_password", ""), data.get("new_password", ""))
        return jsonify({"status": "password updated"})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


# User management (manager/admin only)
@app.route("/api/users", methods=["GET"])
def get_users():
    user, err = require_manager(request)
    if err:
        return err
    return jsonify(list_users())


@app.route("/api/users", methods=["POST"])
def create_new_user():
    user, err = require_manager(request)
    if err:
        return err
    data = request.get_json(force=True)
    if data.get("role") == "admin" and user["role"] != "admin":
        return jsonify({"error": "only an admin can create admin accounts"}), 403
    try:
        new_user = create_user(
            email=data.get("email", ""),
            password=data.get("password", ""),
            name=data.get("name", ""),
            role=data.get("role", "agent"),
            agent_id=data.get("agent_id"),
        )
        return jsonify(new_user), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/users/<int:user_id>", methods=["PATCH"])
def update_existing_user(user_id):
    user, err = require_manager(request)
    if err:
        return err
    data = request.get_json(force=True)
    if data.get("role") == "admin" and user["role"] != "admin":
        return jsonify({"error": "only an admin can grant the admin role"}), 403
    try:
        updated = update_user(user_id, data)
        return jsonify(updated)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/properties")
def list_properties():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM properties ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/leads")
def list_leads():
    user = g.get("user")                 # set by enforce_access(); None only in guest/demo mode
    conn = get_connection()
    if user is None or user["role"] in _MANAGER_ROLES:
        rows = conn.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM leads WHERE assigned_agent_id = ? OR assigned_agent_id IS NULL ORDER BY created_at DESC",
            (user["agent_id"],)
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/leads/<int:lead_id>")
def get_lead(lead_id):
    conn = get_connection()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        conn.close()
        return jsonify({"error": "not found"}), 404
    events = conn.execute(
        "SELECT * FROM lead_events WHERE lead_id = ? ORDER BY created_at DESC", (lead_id,)
    ).fetchall()
    conn.close()

    lead_dict = dict(lead)
    # Phase 1 additions — parse the JSON-stored fields for the frontend, and compute
    # missing-field / next-question guidance fresh each time (never stored, always
    # reflects the CURRENT profile state so it can't go stale)
    lead_dict["objections"] = json.loads(lead["objections"]) if lead["objections"] else []
    lead_dict["extraction_confidence"] = json.loads(lead["extraction_confidence"]) if lead["extraction_confidence"] else {}
    lead_dict["missing_fields"] = compute_missing_fields(lead)
    lead_dict["suggested_next_question"] = suggest_next_question(lead)

    return jsonify({"lead": lead_dict, "events": [dict(e) for e in events]})


@app.route("/api/leads/<int:lead_id>/matches")
def get_matches(lead_id):
    top_n = max(1, min(request.args.get("top_n", 5, type=int), 100))
    try:
        matches = match_lead_to_properties(lead_id, top_n=top_n)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(matches)


@app.route("/api/leads", methods=["POST"])
def create_lead():
    """Agent-entered lead — this is a REAL lead being actively worked by an agent,
    not anonymized research data, so name/phone are legitimately collected here
    (an agent needs to actually contact their customer). This is a different data
    flow from scripts/ingest_brokerage_data.py, which is for anonymized bulk
    historical data used for pilot validation, not live operational leads — see
    docs/DATA_PROCESSING_POLICY.md for that distinction. Always tagged data_source='real'."""
    data = request.get_json(force=True)
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400
    conn = get_connection()
    cur = conn.execute("""
        INSERT INTO leads (name, phone, channel, data_source, budget_min, budget_max, payment_preference,
                            max_monthly_payment, max_down_payment, preferred_locations,
                            min_bedrooms, purpose, purchase_timeline, intent)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        data.get("name"), data.get("phone"), data.get("channel", "whatsapp"), "real",
        data.get("budget_min"), data.get("budget_max"), data.get("payment_preference", "unknown"),
        data.get("max_monthly_payment"), data.get("max_down_payment"), data.get("preferred_locations", ""),
        data.get("min_bedrooms"), data.get("purpose", "unknown"), data.get("purchase_timeline", "unknown"),
        data.get("intent", "unknown"),
    ))
    conn.commit()
    lead_id = cur.lastrowid
    conn.close()
    user = g.get("user")
    if user is not None and user["role"] not in _MANAGER_ROLES and user["agent_id"]:
        assign_lead(lead_id, user["agent_id"], assigned_by=user["email"])
    return jsonify({"id": lead_id}), 201


@app.route("/api/leads/<int:lead_id>", methods=["PUT"])
def update_lead(lead_id):
    """Edit an existing lead's profile fields. Only updates fields present in the
    request body — partial updates are fine, this isn't a full-replace."""
    data = request.get_json(force=True)
    conn = get_connection()
    existing = conn.execute("SELECT id FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "not found"}), 404

    editable_fields = ["name", "phone", "channel", "budget_min", "budget_max", "payment_preference",
                        "max_monthly_payment", "max_down_payment", "preferred_locations",
                        "min_bedrooms", "purpose", "purchase_timeline", "intent",
                        "urgency", "delivery_preference"]
    updates = {k: v for k, v in data.items() if k in editable_fields}
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE leads SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
                     (*updates.values(), lead_id))
        conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/leads/<int:lead_id>", methods=["DELETE"])
def delete_lead(lead_id):
    conn = get_connection()
    existing = conn.execute("SELECT id FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "not found"}), 404
    # Cascading deletes (lead_events, matches, lead_scores, next_actions, outcomes)
    # are handled by SQLite's ON DELETE CASCADE per the schema in db.py.
    conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})


@app.route("/api/leads/<int:lead_id>/status", methods=["PUT"])
def set_lead_status(lead_id):
    """Pipeline stage transition via pipeline.py state machine. Pass lost_reason when moving to lost."""
    data = request.get_json(force=True)
    new_status = data.get("status")
    if not new_status or new_status not in ALL_VALID_STATUSES:
        return jsonify({"error": f"status must be one of {sorted(ALL_VALID_STATUSES)}"}), 400
    result = transition_lead(
        lead_id, new_status,
        actor=(g.user["email"] if g.get("user") is not None else data.get("actor", "agent")),
        reason=data.get("reason"),
        notes=data.get("notes"),
        lost_reason=data.get("lost_reason"),
    )
    if not result["ok"]:
        code = 404 if "not found" in result["error"] else 422
        return jsonify({"error": result["error"]}), code
    return jsonify(result)


@app.route("/api/leads/<int:lead_id>/events", methods=["POST"])
def add_event(lead_id):
    data = request.get_json(force=True)
    conn = get_connection()
    conn.execute("""
        INSERT INTO lead_events (lead_id, event_type, payload) VALUES (?,?,?)
    """, (lead_id, data.get("event_type", "message_in"), data.get("payload", "")))
    conn.execute("UPDATE leads SET updated_at = datetime('now') WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"}), 201


# ---------------------------------------------------------------------------
# Phase 2 — WhatsApp-style inbound message + extraction
# Phase 3 — extended to also generate a suggested reply (Conversation Copilot)
# ---------------------------------------------------------------------------
@app.route("/api/leads/<int:lead_id>/chat", methods=["POST"])
def chat_message(lead_id):
    """Simulates an inbound WhatsApp message from a lead: logs the raw message,
    runs extraction (now including Phase 3's removed-location/engagement-signal
    detection), merges the profile, and composes a suggested reply grounded in
    the lead's updated profile and real current inventory matches."""
    data = request.get_json(force=True)
    message = data.get("message", "")
    if not message.strip():
        return jsonify({"error": "message required"}), 400

    try:
        result = generate_conversation_reply(lead_id, message)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404

    return jsonify(result)


@app.route("/api/whatsapp/webhook", methods=["GET"])
def whatsapp_verify():
    """Meta's one-time handshake when you register the webhook URL."""
    expected = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
    given = request.args.get("hub.verify_token", "")
    if request.args.get("hub.mode") == "subscribe" and expected and hmac.compare_digest(given, expected):
        return request.args.get("hub.challenge", ""), 200
    return jsonify({"error": "verification failed"}), 403


@app.route("/api/whatsapp/webhook", methods=["POST"])
def whatsapp_webhook():
    """Real WhatsApp Business API webhook shape (Meta Cloud API format). Not
    reachable from outside this sandbox, but this is the actual endpoint you'd
    point Meta's webhook config at in production — parses their payload shape
    and delegates to the same conversation-copilot logic as chat_message()."""
    if _auth_required() or os.environ.get("WHATSAPP_APP_SECRET"):
        # public URL: only Meta, who signs the body with the app secret, may post here
        if not _verify_meta_signature(request.get_data()):
            return jsonify({"error": "invalid signature"}), 403
    payload = request.get_json(force=True)
    try:
        entry = payload["entry"][0]["changes"][0]["value"]
    except (KeyError, IndexError, TypeError):
        return jsonify({"error": "unrecognized payload shape"}), 400
    messages = entry.get("messages") or []
    if not messages:
        # delivery/read receipts and other non-message events: acknowledge so Meta does not retry
        return jsonify({"status": "ignored"}), 200
    try:
        message = messages[0]["text"]["body"]
        from_number = messages[0]["from"]
    except (KeyError, IndexError, TypeError):
        # image / voice note / etc.: not handled yet, acknowledge so Meta does not retry
        return jsonify({"status": "ignored", "reason": "non-text message"}), 200

    conn = get_connection()
    lead = conn.execute("SELECT id FROM leads WHERE phone = ?", (from_number,)).fetchone()
    if lead:
        lead_id = lead["id"]
    else:
        cur = conn.execute("INSERT INTO leads (name, phone, channel, data_source) VALUES (?,?, 'whatsapp', 'real')",
                            (f"WhatsApp {from_number}", from_number))
        lead_id = cur.lastrowid
        conn.commit()
    conn.close()

    # generate_conversation_reply() logs the inbound message itself, so no
    # separate message_in insert is needed here (was previously duplicated
    # logic — now this webhook and chat_message() share one code path).
    result = generate_conversation_reply(lead_id, message)
    return jsonify({"status": "ok", "lead_id": lead_id, "suggested_reply": result["suggested_reply"]})


# ---------------------------------------------------------------------------
# Phase 3 — Lead scoring
# ---------------------------------------------------------------------------
@app.route("/api/leads/<int:lead_id>/score", methods=["GET", "POST"])
def get_score(lead_id):
    try:
        result = score_lead(lead_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    result["top_factors_readable"] = humanize_factors(result["top_factors"])
    return jsonify(result)


# ---------------------------------------------------------------------------
# Phase 4 — Next Best Action
# ---------------------------------------------------------------------------
@app.route("/api/leads/<int:lead_id>/next-action", methods=["GET", "POST"])
def get_next_action(lead_id):
    try:
        result = generate_next_action(lead_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(result)


@app.route("/api/leads/<int:lead_id>/next-action/track", methods=["POST"])
def track_next_action(lead_id):
    """Section 13: records that an agent acted on (or completed) a recommendation,
    as a lead_event — feeds future analysis of which recommended actions actually
    produce results."""
    data = request.get_json(force=True)
    event = data.get("event")
    if event not in ("selected", "completed"):
        return jsonify({"error": "event must be 'selected' or 'completed'"}), 400
    try:
        track_next_action_event(lead_id, event, data.get("note", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "ok"})


@app.route("/api/priority-queue")
def priority_queue():
    limit = max(1, min(request.args.get("limit", 20, type=int), 200))
    ids = _accessible_lead_ids(g.get("user"))
    if ids is None:
        return jsonify(get_priority_queue(limit=limit))
    queue = get_priority_queue(limit=1000)
    return jsonify([q for q in queue if q["lead_id"] in ids][:limit])


# ---------------------------------------------------------------------------
# Phase 4 — Advanced Installment / Affordability Intelligence
# ---------------------------------------------------------------------------
@app.route("/api/leads/<int:lead_id>/properties/<int:property_id>/affordability")
def property_affordability(lead_id, property_id):
    try:
        return jsonify(compare_property_plans(property_id, lead_id))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404


@app.route("/api/leads/<int:lead_id>/affordability-profile")
def affordability_profile(lead_id):
    try:
        return jsonify(get_affordability_profile(lead_id))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404


# ---------------------------------------------------------------------------
# Automated Valuation Model (AVM) endpoints
# ---------------------------------------------------------------------------
@app.route("/api/properties/<int:property_id>/valuation", methods=["GET"])
def property_valuation(property_id):
    try:
        val = estimate_property_value(property_id)
        return jsonify(val)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Valuation error: {str(e)}"}), 500


@app.route("/api/valuation/predict", methods=["POST"])
def valuation_predict():
    data = request.get_json(force=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "JSON body required"}), 400
    try:
        val = estimate_property_value(data)
        return jsonify(val)
    except Exception as e:
        return jsonify({"error": f"Valuation error: {str(e)}"}), 500


@app.route("/api/valuation/metrics", methods=["GET"])
def valuation_metrics():
    try:
        metrics = get_valuation_metrics()
        return jsonify(metrics)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Phase 5 — Outcomes, feedback loop, manager pipeline view
# ---------------------------------------------------------------------------
@app.route("/api/leads/<int:lead_id>/outcome", methods=["POST"])
def post_outcome(lead_id):
    data = request.get_json(force=True)
    outcome = data.get("outcome")
    if outcome not in ("contacted", "viewing_booked", "negotiating", "won", "lost", "no_response"):
        return jsonify({"error": "invalid outcome"}), 400
    log_outcome(lead_id, outcome, data.get("note", ""), data.get("property_id"))
    return jsonify({"status": "ok"})


@app.route("/api/pipeline")
def get_pipeline():
    return jsonify(pipeline_summary())


@app.route("/api/stats")
def get_stats():
    """Powers the DATA MODE indicator — always visible so it's never ambiguous
    whether what's on screen is demo/synthetic data or real leads/properties.
    Also powers the model-reliability indicator (see feedback.get_model_maturity)."""
    conn = get_connection()
    stats = {}
    user = g.get("user")                 # agents only count the leads they are allowed to see
    if user is None or user["role"] in _MANAGER_ROLES:
        lead_rows = conn.execute("SELECT data_source, COUNT(*) c FROM leads GROUP BY data_source").fetchall()
    else:
        lead_rows = conn.execute("SELECT data_source, COUNT(*) c FROM leads WHERE assigned_agent_id IS NULL "
                                 "OR assigned_agent_id = ? GROUP BY data_source", (user["agent_id"],)).fetchall()
    for row in lead_rows:
        stats[f"leads_{row['data_source']}"] = row["c"]
    for row in conn.execute("SELECT data_source, COUNT(*) c FROM properties GROUP BY data_source"):
        stats[f"properties_{row['data_source']}"] = row["c"]
    real_outcome_count = conn.execute("SELECT COUNT(*) c FROM outcomes").fetchone()["c"]
    conn.close()

    maturity = get_model_maturity(real_outcome_count)

    return jsonify({
        "demo_leads": stats.get("leads_synthetic", 0),
        "real_leads": stats.get("leads_real", 0),
        "real_properties": sum(v for k, v in stats.items() if k.startswith("properties_real")),
        "synthetic_properties": stats.get("properties_synthetic", 0),
        # Model reliability / data maturity — additive, existing fields above unchanged
        "real_outcomes": real_outcome_count,
        "model_maturity": maturity["maturity"],
        "model_reliability": maturity["reliability"],
        "score_warning": maturity["warning"],
    })


@app.route("/api/retrain", methods=["POST"])
def retrain():
    retrain_from_outcomes()
    return jsonify({"status": "retrained"})


# ---------------------------------------------------------------------------
# Pipeline history (new - state machine)
# ---------------------------------------------------------------------------
@app.route("/api/leads/<int:lead_id>/pipeline-history")
def lead_pipeline_history(lead_id):
    """Returns the complete ordered pipeline transition history for a lead."""
    conn = get_connection()
    lead = conn.execute("SELECT id FROM leads WHERE id = ?", (lead_id,)).fetchone()
    conn.close()
    if not lead:
        return jsonify({"error": "not found"}), 404
    return jsonify(get_pipeline_history(lead_id))


# ---------------------------------------------------------------------------
# AI Lead Intelligence (Phase 5 — rich qualification report)
# ---------------------------------------------------------------------------
@app.route("/api/leads/<int:lead_id>/intelligence")
def lead_intelligence(lead_id):
    """Returns a rich AI lead intelligence report: score, priority tier,
    positive factors, warnings, missing info, and next question to ask."""
    try:
        return jsonify(get_lead_intelligence(lead_id))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Intelligence error: {str(e)}"}), 500


# ---------------------------------------------------------------------------
# Sales Analytics — Manager Dashboard
# ---------------------------------------------------------------------------
@app.route("/api/analytics/manager")
def manager_dashboard():
    """Real KPIs calculated from database records. The data_basis field
    ('real', 'synthetic', or 'mixed') indicates provenance of the numbers."""
    try:
        return jsonify(get_manager_dashboard())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analytics/funnel")
def conversion_funnel():
    """Stage-by-stage conversion funnel with rates between each stage."""
    try:
        return jsonify(get_conversion_funnel())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analytics/agents")
def agents_performance():
    """Per-agent productivity summary (leads assigned, won, lost, win rate)."""
    agent_id = request.args.get("agent_id", type=int)
    try:
        return jsonify(get_agent_performance(agent_id=agent_id))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Data Quality endpoint (Phase 1)
# ---------------------------------------------------------------------------
@app.route("/api/data-quality")
def data_quality():
    """Property and lead data quality metrics, all computed from the database."""
    try:
        return jsonify(get_data_quality_metrics())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Sales Agents CRUD
# ---------------------------------------------------------------------------
@app.route("/api/agents", methods=["GET"])
def get_agents():
    active_only = request.args.get("active_only", "true").lower() != "false"
    return jsonify(list_agents(active_only=active_only))


@app.route("/api/agents", methods=["POST"])
def create_new_agent():
    data = request.get_json(force=True)
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400
    agent_id = create_agent(
        name=data["name"],
        email=data.get("email"),
        phone=data.get("phone"),
    )
    return jsonify({"id": agent_id}), 201


@app.route("/api/agents/<int:agent_id>")
def get_single_agent(agent_id):
    agent = get_agent(agent_id)
    if not agent:
        return jsonify({"error": "not found"}), 404
    return jsonify(agent)


@app.route("/api/agents/<int:agent_id>/deactivate", methods=["POST"])
def deactivate_single_agent(agent_id):
    ok = deactivate_agent(agent_id)
    if not ok:
        return jsonify({"error": "not found"}), 404
    return jsonify({"status": "deactivated"})


# ---------------------------------------------------------------------------
# Lead Assignment
# ---------------------------------------------------------------------------
@app.route("/api/leads/<int:lead_id>/assign", methods=["POST"])
def assign_lead_to_agent(lead_id):
    data = request.get_json(force=True)
    agent_id = data.get("agent_id")
    if not agent_id:
        return jsonify({"error": "agent_id is required"}), 400
    user = g.get("user")
    result = assign_lead(lead_id, agent_id,
                         assigned_by=(user["email"] if user is not None else data.get("assigned_by")))
    if not result["ok"]:
        return jsonify({"error": result["error"]}), 404
    return jsonify(result)


@app.route("/api/leads/<int:lead_id>/assignments")
def lead_assignments(lead_id):
    conn = get_connection()
    lead = conn.execute("SELECT id FROM leads WHERE id = ?", (lead_id,)).fetchone()
    conn.close()
    if not lead:
        return jsonify({"error": "not found"}), 404
    return jsonify(get_assignment_history(lead_id))


def load_seed_properties():
    """First start on a fresh server: the database is empty and the shell is not available on every
    hosting plan, so load the listings automatically. Runs only when TASAHEL_SEED_CSV is set, the
    properties table is empty and the file exists, so a restart never re-imports or duplicates anything.
    A failure is logged and never stops the server from starting."""
    csv_setting = os.environ.get("TASAHEL_SEED_CSV", "").strip()
    if not csv_setting:
        return
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    csv_path = csv_setting if os.path.isabs(csv_setting) else os.path.join(root, csv_setting)
    conn = get_connection()
    existing = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    conn.close()
    if existing > 0:
        return
    if not os.path.exists(csv_path):
        print(f"[bootstrap] TASAHEL_SEED_CSV points to a missing file: {csv_path}")
        return
    import subprocess
    import sys
    env = dict(os.environ, COPILOT_DB_PATH=str(DB_PATH))
    steps = [
        [sys.executable, os.path.join(root, "scripts", "ingest_kaggle.py"), "--csv", csv_path],
        [sys.executable, os.path.join(root, "scripts", "dedupe_properties.py"), "--apply", "--no-backup"],
    ]
    for cmd in steps:
        try:
            result = subprocess.run(cmd, cwd=root, env=env, timeout=1200, check=False)
            print(f"[bootstrap] {os.path.basename(cmd[1])} finished with exit code {result.returncode}")
            if result.returncode != 0:
                break
        except Exception as e:                       # noqa: BLE001 - never block startup
            print(f"[bootstrap] {os.path.basename(cmd[1])} failed: {e}")
            break


def bootstrap():
    """Create/upgrade the schema and seed default accounts and agents. Under gunicorn the __main__
    block below never runs, so deployments set TASAHEL_BOOTSTRAP=1 (and start gunicorn with --preload
    so it runs once, not once per worker)."""
    if not os.path.exists(str(DB_PATH)):
        init_db()
    else:
        migrate_schema()  # cheap, idempotent: picks up new columns and tables on existing databases
    seed_default_users()   # creates default accounts if no users exist yet
    seed_default_agents()  # creates default sales agents if none exist yet
    load_seed_properties() # loads the listings on a fresh server (only if TASAHEL_SEED_CSV is set)


if os.environ.get("TASAHEL_BOOTSTRAP", "0").strip().lower() in ("1", "true", "yes"):
    bootstrap()


if __name__ == "__main__":
    bootstrap()
    if not _auth_required():
        print("WARNING: TASAHEL_REQUIRE_AUTH is not set. Every /api route is open (demo mode). "
              "Do not expose this server to the internet like this.")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)), debug=False, threaded=True)
