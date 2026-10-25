"""
Regression tests for the lead-intelligence / matching consistency bugs:

  * "0 Matching Units" + "Exceeds Budget" next to a 92/100 high-priority score
  * match counts that grew every time the page was opened (counted rows of the matches log)
  * "Purchase timeline not stated" for a lead whose timeline is 'immediate'
  * "High Installment" shown when no limits were stated (unknown, not a failure)

Self-contained: builds a temporary SQLite DB and patches get_connection in the two modules,
so it does not touch the real copilot.db or the saved model.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import matching  # noqa: E402
import scoring   # noqa: E402

SCHEMA = """
CREATE TABLE leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, phone TEXT, channel TEXT DEFAULT 'whatsapp',
  data_source TEXT DEFAULT 'synthetic', source TEXT, assigned_agent_id INTEGER,
  budget_min REAL, budget_max REAL, payment_preference TEXT DEFAULT 'unknown',
  max_monthly_payment REAL, max_down_payment REAL, preferred_locations TEXT, min_bedrooms INTEGER,
  purpose TEXT, purchase_timeline TEXT DEFAULT 'unknown', intent TEXT, status TEXT DEFAULT 'new',
  lost_reason TEXT, urgency TEXT DEFAULT 'unknown', delivery_preference TEXT, objections TEXT,
  extraction_confidence REAL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT);
CREATE TABLE properties (
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, compound TEXT, location TEXT, property_type TEXT,
  area_sqm REAL, bedrooms INTEGER, bathrooms INTEGER, floor INTEGER, finishing TEXT,
  has_pool INTEGER DEFAULT 0, has_garden INTEGER DEFAULT 0, has_parking INTEGER DEFAULT 1,
  has_security INTEGER DEFAULT 1, developer TEXT, cash_price REAL, delivery_year INTEGER,
  status TEXT DEFAULT 'available', description TEXT, data_source TEXT DEFAULT 'real_kaggle',
  source_url TEXT, provenance_details TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE installment_plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT, property_id INTEGER, years INTEGER, down_payment_pct REAL,
  monthly_payment REAL, total_price REAL, plan_source TEXT, extracted_terms TEXT);
CREATE TABLE lead_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, event_type TEXT, payload TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE matches (
  id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, property_id INTEGER, score REAL, reasons TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE lead_scores (
  id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, score REAL, conversion_probability REAL,
  top_factors TEXT, model_version TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""


@pytest.fixture(scope="module")
def model_path(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("model") / "lead_model.pkl")
    old = scoring.MODEL_PATH
    scoring.MODEL_PATH = path
    try:
        scoring.train_model()          # train once for the whole module, into a temp file
    finally:
        scoring.MODEL_PATH = old
    return path


@pytest.fixture
def db(tmp_path, monkeypatch, model_path):
    path = str(tmp_path / "t.db")
    setup = sqlite3.connect(path)
    setup.executescript(SCHEMA)
    setup.commit()
    setup.close()

    def connect():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(matching, "get_connection", connect)
    monkeypatch.setattr(scoring, "get_connection", connect)
    monkeypatch.setattr(scoring, "MODEL_PATH", model_path)
    scoring.clear_fit_cache()
    yield connect
    scoring.clear_fit_cache()


def add_property(connect, title, price, location="New Cairo", beds=3, monthly=None, years=7,
                 down_pct=10.0, plan_source="extracted_duration"):
    c = connect()
    cur = c.execute(
        "INSERT INTO properties (title, compound, location, property_type, area_sqm, bedrooms, finishing,"
        " cash_price) VALUES (?,?,?,?,?,?,?,?)",
        (title, title, location, "apartment", 170, beds, "fully-finished", price))
    pid = cur.lastrowid
    if monthly is not None:
        total = price
        c.execute("INSERT INTO installment_plans (property_id, years, down_payment_pct, monthly_payment,"
                  " total_price, plan_source) VALUES (?,?,?,?,?,?)",
                  (pid, years, down_pct, monthly, total, plan_source))
    c.commit()
    c.close()
    return pid


def add_lead(connect, **kw):
    base = dict(name="Hesham", phone="01000000101", channel="whatsapp", budget_min=7_000_000,
                budget_max=9_000_000, payment_preference="installment", max_monthly_payment=90_000,
                max_down_payment=900_000, preferred_locations="New Cairo", min_bedrooms=3,
                purchase_timeline="immediate")
    base.update(kw)
    cols = ",".join(base)
    c = connect()
    cur = c.execute(f"INSERT INTO leads ({cols}) VALUES ({','.join('?' * len(base))})", tuple(base.values()))
    c.commit()
    lid = cur.lastrowid
    c.close()
    return lid


def seed_hesham_inventory(connect):
    good = [
        add_property(connect, "Moon Residences", 7_500_000, monthly=75_000),
        add_property(connect, "JAYD Residence", 8_250_000, monthly=80_000),
        add_property(connect, "Icon Plaza", 8_000_000, monthly=70_000),
    ]
    bad = {
        "too_expensive": add_property(connect, "The Crest", 10_900_000, monthly=60_000),  # +21% over max
        "far_below":     add_property(connect, "Icon Small", 4_500_000, monthly=48_000),
        "wrong_area":    add_property(connect, "Maadi Corniche", 8_000_000, location="Maadi", monthly=70_000),
        "few_beds":      add_property(connect, "Studio-ish", 8_000_000, beds=2, monthly=70_000),
        "plan_too_big":  add_property(connect, "Pricey Plan", 8_500_000, monthly=150_000),
    }
    return good, bad


# ---------------------------------------------------------------------------
def test_matching_counts_only_units_that_meet_requirements(db):
    good, bad = seed_hesham_inventory(db)
    lid = add_lead(db)
    fit = scoring.get_fit_summary(lid)
    assert fit["qualified_count"] == len(good)
    assert fit["budget_status"] == "fit"
    assert fit["location_status"] == "fit"
    assert fit["financing_status"] == "fit"


def test_intelligence_for_a_healthy_lead_is_consistent(db):
    seed_hesham_inventory(db)
    lid = add_lead(db)
    intel = scoring.get_lead_intelligence(lid)
    assert intel["budget_status"] == "fit" and intel["budget_fit"] is True
    assert intel["financing_status"] == "fit"
    assert intel["inventory_availability"]["total_matches"] == 3
    assert intel["score_capped"] is False
    assert "Purchase timeline not stated" not in intel["warnings"]      # timeline='immediate'
    assert intel["urgency"] == {"label": "high", "source": "immediate"}


def test_no_fitting_inventory_caps_the_score_and_says_so(db):
    # only units that cannot satisfy this lead exist
    add_property(db, "The Crest", 10_900_000, monthly=60_000)
    add_property(db, "Icon Small", 4_500_000, monthly=48_000)
    add_property(db, "Maadi Corniche", 8_000_000, location="Maadi", monthly=70_000)
    lid = add_lead(db)
    intel = scoring.get_lead_intelligence(lid)
    assert intel["inventory_availability"]["total_matches"] == 0
    assert intel["budget_status"] == "gap" and intel["budget_fit"] is False
    assert intel["score_capped"] is True
    assert intel["score"] <= scoring.NO_FIT_SCORE_CAP
    assert intel["priority"] == "low"                  # never "high priority" with nothing to offer
    assert any("capped" in w for w in intel["warnings"])


def test_score_lead_applies_the_same_cap_for_other_endpoints(db):
    add_property(db, "Maadi Corniche", 8_000_000, location="Maadi", monthly=70_000)
    lid = add_lead(db)
    result = scoring.score_lead(lid)
    assert result["score"] <= scoring.NO_FIT_SCORE_CAP
    stored = db().execute("SELECT score FROM lead_scores WHERE lead_id = ?", (lid,)).fetchone()["score"]
    assert stored == result["score"]                   # what next_action / priority queue reads


def test_counts_do_not_grow_when_the_page_is_opened_repeatedly(db):
    seed_hesham_inventory(db)
    lid = add_lead(db)
    first = scoring.get_lead_intelligence(lid)["inventory_availability"]["total_matches"]
    for _ in range(4):
        matching.match_lead_to_properties(lid, top_n=5)      # what GET /matches does
        again = scoring.get_lead_intelligence(lid)["inventory_availability"]["total_matches"]
        assert again == first
    rows = db().execute("SELECT COUNT(*) c FROM matches WHERE lead_id = ?", (lid,)).fetchone()["c"]
    assert rows <= 20                                        # latest result set only, not 5 x N


def test_unknown_urgency_column_does_not_hide_a_real_timeline(db):
    seed_hesham_inventory(db)
    lid = add_lead(db, purchase_timeline="1-3m")             # urgency column keeps its 'unknown' default
    intel = scoring.get_lead_intelligence(lid)
    assert intel["urgency"]["label"] == "high"
    assert "Purchase timeline not stated" not in intel["warnings"]


def test_genuinely_missing_timeline_still_warns(db):
    seed_hesham_inventory(db)
    lid = add_lead(db, purchase_timeline="unknown")
    intel = scoring.get_lead_intelligence(lid)
    assert "Purchase timeline not stated" in intel["warnings"]


def test_installment_without_limits_is_unknown_not_a_failure(db):
    seed_hesham_inventory(db)
    lid = add_lead(db, max_monthly_payment=None, max_down_payment=None)
    intel = scoring.get_lead_intelligence(lid)
    assert intel["financing_status"] == "unknown"
    assert intel["financing_fit"] is False                   # boolean kept for old callers...
    assert not any(w.startswith("Financing gap") for w in intel["warnings"])   # ...but not reported as a gap


def test_missing_budget_is_unknown_not_exceeds_budget(db):
    seed_hesham_inventory(db)
    lid = add_lead(db, budget_min=None, budget_max=None)
    intel = scoring.get_lead_intelligence(lid)
    assert intel["budget_status"] == "unknown"


def test_cash_buyer_financing_is_fit_without_plans(db):
    add_property(db, "Chalet A", 10_000_000, location="North Coast")        # no plan at all
    lid = add_lead(db, payment_preference="cash", budget_min=9_000_000, budget_max=12_000_000,
                   max_monthly_payment=None, max_down_payment=None, preferred_locations="North Coast")
    intel = scoring.get_lead_intelligence(lid)
    assert intel["financing_status"] == "fit"
    assert intel["financing_fit_reason"].startswith("Cash buyer")
    assert intel["inventory_availability"]["total_matches"] == 1


def test_editing_the_lead_invalidates_the_cache(db):
    seed_hesham_inventory(db)
    lid = add_lead(db)
    assert scoring.get_fit_summary(lid)["qualified_count"] == 3
    c = db()
    c.execute("UPDATE leads SET budget_max = 5000000, budget_min = 4000000 WHERE id = ?", (lid,))
    c.commit()
    c.close()
    # new budget 4-5M: only the 4.5M unit qualifies now (and it must not come from a stale cache)
    assert scoring.get_fit_summary(lid)["qualified_count"] == 1


def test_new_inventory_invalidates_the_cache(db):
    seed_hesham_inventory(db)
    lid = add_lead(db)
    assert scoring.get_fit_summary(lid)["qualified_count"] == 3
    add_property(db, "New Launch", 8_400_000, monthly=82_000)
    assert scoring.get_fit_summary(lid)["qualified_count"] == 4


def test_lead_with_no_requirements_is_not_capped(db):
    seed_hesham_inventory(db)
    lid = add_lead(db, budget_min=None, budget_max=None, payment_preference="unknown",
                   max_monthly_payment=None, max_down_payment=None, preferred_locations=None,
                   min_bedrooms=None)
    result = scoring.score_lead(lid)
    assert result["score_capped"] is False


def test_ranking_is_not_a_flat_wall_of_identical_scores(db):
    seed_hesham_inventory(db)
    lid = add_lead(db)
    top = matching.match_lead_to_properties(lid, top_n=3)
    assert len({m["score"] for m in top}) > 1
