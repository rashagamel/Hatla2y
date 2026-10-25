"""
SQLite database setup for the Real Estate AI Sales Copilot.

Schema:
  properties        - core property listing
  installment_plans  - one property can have multiple payment plans
  leads              - a customer inquiry
  lead_events        - raw conversation / activity log per lead (feeds extraction + scoring)
  matches            - cached match results between a lead and properties
"""
import sqlite3
import os

DEFAULT_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "processed", "copilot.db"))
_CURRENT_DB_PATH = None


def get_db_path() -> str:
    if _CURRENT_DB_PATH is not None:
        return _CURRENT_DB_PATH
    return os.environ.get("COPILOT_DB_PATH", DEFAULT_DB_PATH)


def set_db_path(path: str):
    """Override database path (used by test suites for isolated temporary databases)."""
    global _CURRENT_DB_PATH
    _CURRENT_DB_PATH = os.path.abspath(path) if path else None


def reset_db_path():
    """Reset database path to default / environment variable."""
    global _CURRENT_DB_PATH
    _CURRENT_DB_PATH = None


class _DbPathProxy(str):
    """Dynamic proxy for DB_PATH so references to db.DB_PATH always resolve to the active path."""
    def __new__(cls):
        return super().__new__(cls, get_db_path())
    def __str__(self):
        return get_db_path()
    def __repr__(self):
        return repr(get_db_path())
    def __fspath__(self):
        return get_db_path()


DB_PATH = _DbPathProxy()


def get_connection(db_path: str = None):
    path = db_path or get_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS properties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    compound TEXT,
    location TEXT NOT NULL,          -- e.g. "New Cairo"
    property_type TEXT NOT NULL,     -- apartment | villa | duplex | townhouse
    area_sqm REAL NOT NULL,
    bedrooms INTEGER NOT NULL,
    bathrooms INTEGER NOT NULL,
    floor INTEGER,
    finishing TEXT,                  -- core | semi-finished | fully-finished | luxury
    has_pool INTEGER DEFAULT 0,
    has_garden INTEGER DEFAULT 0,
    has_parking INTEGER DEFAULT 0,
    has_security INTEGER DEFAULT 0,
    developer TEXT,
    cash_price REAL NOT NULL,        -- EGP
    delivery_year INTEGER,
    status TEXT DEFAULT 'available', -- available | reserved | sold
    description TEXT,
    data_source TEXT DEFAULT 'synthetic',  -- synthetic | real_kaggle | real_brokerage | real_market_calibrated
    source_url TEXT,                 -- URL or original portal reference if available
    provenance_details TEXT,         -- JSON holding raw ingestion source, date, price semantics
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS installment_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    years INTEGER NOT NULL,
    down_payment_pct REAL NOT NULL,   -- e.g. 10 = 10%
    monthly_payment REAL NOT NULL,    -- EGP/month
    total_price REAL NOT NULL,        -- installment total (usually > cash_price)
    plan_source TEXT DEFAULT 'assumed_default',  -- extracted_from_description | structured_listing | assumed_default
    extracted_terms TEXT             -- JSON holding raw extracted numbers/tokens from description
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT,
    channel TEXT DEFAULT 'whatsapp',  -- whatsapp | facebook_ad | call | walk_in | referral | website
    data_source TEXT DEFAULT 'synthetic',  -- synthetic | real — tracks provenance so
                                            -- retraining/reporting can distinguish real signal
                                            -- from the synthetic baseline honestly
    source TEXT DEFAULT 'unknown',           -- lead acquisition source: facebook_ad | walk_in | referral | website | ...
    assigned_agent_id INTEGER REFERENCES sales_agents(id),
    -- extracted / evolving profile fields
    budget_min REAL,
    budget_max REAL,
    payment_preference TEXT,          -- cash | installment | unknown
    max_monthly_payment REAL,
    max_down_payment REAL,
    preferred_locations TEXT,         -- comma-separated
    min_bedrooms INTEGER,
    purpose TEXT,                     -- family | investment | rental | unknown
    purchase_timeline TEXT,           -- immediate | 1-3m | 3-6m | 6m+ | unknown
    intent TEXT DEFAULT 'unknown',    -- low | medium | high
    status TEXT DEFAULT 'new',        -- new | contacted | qualified | interested | site_visit | negotiation | won | lost
                                      -- legacy: viewing_booked (=site_visit), negotiating (=negotiation)
    lost_reason TEXT,                -- price | location | financing | competitor | timing | unresponsive | other
    -- Phase 1 (Conversation Intelligence) additions — all nullable/defaulted so
    -- existing rows and existing code paths are unaffected until populated
    urgency TEXT DEFAULT 'unknown',           -- low | medium | high | unknown
    delivery_preference TEXT,                 -- ready | under_construction | unknown
    objections TEXT,                          -- JSON list, e.g. ["monthly_payment","down_payment"]
    extraction_confidence TEXT,               -- JSON dict: {field: "explicit"|"inferred"}
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sales_agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lead_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    agent_id INTEGER NOT NULL REFERENCES sales_agents(id) ON DELETE CASCADE,
    assigned_at TEXT DEFAULT (datetime('now')),
    assigned_by TEXT,
    is_current INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS pipeline_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor TEXT,          -- agent name or 'system'
    reason TEXT,         -- optional free-text reason
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lead_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,   -- message_in | message_out | property_viewed | call | viewing_booked | outcome
    payload TEXT,               -- free text or JSON string
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    score REAL NOT NULL,
    reasons TEXT,                -- JSON list of strings
    created_at TEXT DEFAULT (datetime('now'))
);

-- Phase 3: lead scoring model outputs (one row per scoring run, keeps history)
CREATE TABLE IF NOT EXISTS lead_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    score REAL NOT NULL,               -- 0-100
    conversion_probability REAL NOT NULL,  -- 0-1
    top_factors TEXT,                  -- JSON list of {factor, direction, weight}
    model_version TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Phase 4: next-best-action recommendations (history, so we can see if agents followed them)
CREATE TABLE IF NOT EXISTS next_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,     -- call | whatsapp | wait | send_properties
    channel TEXT,                  -- whatsapp | call | none
    message_suggestion TEXT,
    reasoning TEXT,
    urgency TEXT,                  -- now | today | this_week | low_priority
    generated_by TEXT DEFAULT 'rule_engine',  -- rule_engine | llm
    created_at TEXT DEFAULT (datetime('now'))
);

-- Phase 5: outcome logging -> feeds the feedback loop / retraining
CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    outcome TEXT NOT NULL,   -- contacted | viewing_booked | negotiating | won | lost | no_response
    note TEXT,
    property_id INTEGER REFERENCES properties(id),
    created_at TEXT DEFAULT (datetime('now'))
);

-- Auth: user accounts for agents and managers
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'agent',  -- agent | manager | admin
    agent_id INTEGER REFERENCES sales_agents(id),  -- linked agent profile (null for managers/admins)
    is_active INTEGER NOT NULL DEFAULT 1,
    last_login TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def init_db(reset: bool = False, db_path: str = None):
    path = db_path or get_db_path()
    if reset and os.path.exists(path):
        os.remove(path)
    conn = get_connection(db_path=path)
    conn.executescript(SCHEMA)
    conn.commit()
    migrate_schema(conn)
    conn.close()


# Columns added after the original tables were created — additive migration
# so existing databases (including any already-shipped copilot.db) pick these up
# without needing a full reset. CREATE TABLE IF NOT EXISTS above only affects
# brand-new databases.
MIGRATIONS = [
    ("leads", "urgency", "TEXT DEFAULT 'unknown'"),
    ("leads", "delivery_preference", "TEXT"),
    ("leads", "objections", "TEXT"),
    ("leads", "extraction_confidence", "TEXT"),
    ("leads", "source", "TEXT DEFAULT 'unknown'"),
    ("leads", "assigned_agent_id", "INTEGER"),
    ("leads", "lost_reason", "TEXT"),
    ("properties", "source_url", "TEXT"),
    ("properties", "provenance_details", "TEXT"),
    ("installment_plans", "plan_source", "TEXT DEFAULT 'assumed_default'"),
    ("installment_plans", "extracted_terms", "TEXT"),
]


def migrate_schema(conn=None):
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()
    # Ensure all tables defined in SCHEMA exist (safe & idempotent)
    conn.executescript(SCHEMA)
    for table, column, coltype in MIGRATIONS:
        existing_cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing_cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    conn.commit()
    if owns_conn:
        conn.close()


if __name__ == "__main__":
    init_db(reset=True)
    print(f"Database initialized at {DB_PATH}")
