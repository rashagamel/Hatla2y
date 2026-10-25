"""
Tasahel full system check.
Run from the project root (server can stay running):
    py verify_system.py
Read-only: it never modifies the database.
"""
import sqlite3, subprocess, sys, os, json, urllib.request

DB = "data/processed/copilot.db"
BASE = "http://127.0.0.1:5050"
results = []


def report(status, name, detail=""):
    icon = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}[status]
    results.append((status, name, detail))
    print(f"{icon} {name}" + (f" -> {detail}" if detail else ""))


def section(t):
    print("\n" + "=" * 8 + f" {t} " + "=" * 8)


# ---------- 1. Files ----------
section("1. Files")
for p in ["app/api.py", "app/extraction.py", "app/matching.py", "app/affordability.py",
          "app/scoring.py", "app/next_action.py", "app/valuation.py", "app/feedback.py",
          "app/pipeline.py", "app/analytics.py", DB,
          "data/processed/lead_scoring_model.pkl", "data/processed/valuation_model.pkl",
          "data/real/egypt_real_estate_listings.csv"]:
    report("PASS" if os.path.exists(p) else "FAIL", p)

# ---------- 2. Test suite ----------
section("2. Automated tests")
try:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "--tb=no"],
                       capture_output=True, text=True, timeout=600)
    tail = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
    report("PASS" if r.returncode == 0 else "FAIL", "pytest", tail)
except Exception as e:
    report("WARN", "pytest could not run", str(e))

# ---------- 3. Database ----------
section("3. Database")
if not os.path.exists(DB):
    print("DB missing, stopping DB checks.")
else:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    tables = [r[0] for r in c.execute("select name from sqlite_master where type='table'")]
    print("Tables:", tables)

    def cols(t):
        return [r[1] for r in c.execute(f"pragma table_info({t})")]

    def has(t, col):
        return t in tables and col in cols(t)

    if "properties" in tables:
        print("properties columns:", cols("properties"))
        print("by data_source:", c.execute(
            "select data_source, count(*) from properties group by 1").fetchall())
        n = c.execute("select count(*) from properties").fetchone()[0]
        report("PASS" if n > 1000 else "WARN", "property count", str(n))

        # null / empty critical fields
        for col in ["price", "area", "bedrooms", "district", "compound", "finishing", "property_type"]:
            if has("properties", col):
                bad = c.execute(
                    f"select count(*) from properties where {col} is null or trim(cast({col} as text))='' "
                    f"or lower(cast({col} as text)) in ('undefined','none','nan','null')").fetchone()[0]
                report("PASS" if bad == 0 else "WARN", f"properties.{col} missing/'undefined'", f"{bad} rows")

        # price per sqm sanity
        if has("properties", "price") and has("properties", "area"):
            rows = c.execute("select price/area as p from properties where area>0 and price>0").fetchall()
            bad = sum(1 for r in rows if r["p"] < 5000 or r["p"] > 400000)
            report("PASS" if bad == 0 else "WARN", "price/sqm outside 5k-400k EGP", f"{bad} of {len(rows)}")

        # duplicates
        if has("properties", "price") and has("properties", "area"):
            d = c.execute("select count(*) from (select 1 from properties "
                          "group by price, area, bedrooms, district having count(*)>5)").fetchone()[0]
            report("PASS" if d == 0 else "WARN", "heavy duplicate groups (>5 identical)", str(d))

    # installment plans
    plan_tables = [t for t in tables if "plan" in t.lower() or "install" in t.lower()]
    print("plan tables:", plan_tables)
    for pt in plan_tables:
        pc = cols(pt)
        print(f"{pt} columns:", pc)
        down = next((x for x in pc if "down" in x.lower()), None)
        monthly = next((x for x in pc if "month" in x.lower()), None)
        years = next((x for x in pc if "year" in x.lower()), None)
        months = next((x for x in pc if x.lower() in ("months", "num_months", "term_months")), None)
        pid = next((x for x in pc if x.lower() in ("property_id", "prop_id")), None)
        if down and monthly and (years or months) and pid:
            term = f"{years}*12" if years else months
            q = (f"select p.price, pl.{down} d, pl.{monthly} m, {term} t from {pt} pl "
                 f"join properties p on p.id = pl.{pid} where p.price>0")
            try:
                rows = c.execute(q).fetchall()
                off = 0
                for r in rows:
                    total = r["d"] + r["m"] * r["t"]
                    if abs(total - r["price"]) / r["price"] > 0.15:
                        off += 1
                report("PASS" if off == 0 else "WARN",
                       f"{pt}: down + monthly*term within 15% of price",
                       f"{off} of {len(rows)} outside")
            except Exception as e:
                report("WARN", f"{pt} consistency check failed", str(e))

    # leads
    if "leads" in tables:
        print("leads columns:", cols("leads"))
        for r in c.execute("select * from leads").fetchall():
            d = dict(r)
            name = d.get("name")
            bmin, bmax = d.get("budget_min"), d.get("budget_max")
            if bmin and bmax and bmin > bmax:
                report("FAIL", f"lead {name}: budget_min > budget_max")
            mm, dp = d.get("max_monthly"), d.get("max_down_payment")
            if bmax and mm and mm * 12 * 10 + (dp or 0) < bmin * 0.5:
                report("WARN", f"lead {name}: monthly/down far below budget", f"{mm}/{dp} vs {bmin}-{bmax}")
        report("PASS", "leads scanned")

# ---------- 4. Live API ----------
section("4. Live API (server must be running)")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


try:
    stats = get("/api/stats")
    report("PASS", "/api/stats", json.dumps(stats)[:200])
    leads = get("/api/leads")
    leads = leads if isinstance(leads, list) else leads.get("leads", [])
    report("PASS", "/api/leads", f"{len(leads)} leads")

    for L in leads:
        lid, name = L.get("id"), L.get("name")
        try:
            matches = get(f"/api/leads/{lid}/matches?top_n=10")
            ml = matches if isinstance(matches, list) else matches.get("matches", [])
            intel = get(f"/api/leads/{lid}/intelligence")
            text = json.dumps(intel, ensure_ascii=False)
            score = intel.get("quality_score") or intel.get("score")
            # contradiction: 0 matches but high score
            if len(ml) == 0 and score and float(score) >= 60:
                report("FAIL", f"lead {name}: 0 matches but score {score}", "scoring ignores failed fit")
            # 'undefined' leaking into payloads
            if "undefined" in json.dumps(ml).lower():
                report("WARN", f"lead {name}: 'undefined' in matches payload")
            # matches outside budget
            bmax = L.get("budget_max")
            if bmax:
                out = [m for m in ml if (m.get("price") or (m.get("property") or {}).get("price") or 0) > bmax * 1.15]
                if out:
                    report("WARN", f"lead {name}: {len(out)}/{len(ml)} matches priced >15% over budget max",
                           "ok only if matching is purely installment-based")
            report("PASS", f"lead {name}: endpoints OK", f"{len(ml)} matches, score {score}")
        except Exception as e:
            report("FAIL", f"lead {name}: endpoint error", str(e))
except Exception as e:
    report("FAIL", "API unreachable (is the server running on 5050?)", str(e))

# ---------- 5. Security ----------
section("5. Security / production readiness")
try:
    urllib.request.urlopen(BASE + "/api/leads", timeout=10)
    report("FAIL", "API open without login", "anyone with the link can read all leads -> do NOT deploy publicly yet")
except Exception:
    report("PASS", "API requires auth")
for f in [".env", ".gitignore", "requirements.txt"]:
    report("PASS" if os.path.exists(f) else "WARN", f"{f} exists")
if os.path.exists(".gitignore"):
    g = open(".gitignore", encoding="utf-8", errors="ignore").read()
    for must in [".env", ".db"]:
        report("PASS" if must in g else "WARN", f".gitignore covers {must}")

# ---------- Summary ----------
section("SUMMARY")
for s in ["FAIL", "WARN", "PASS"]:
    print(s, sum(1 for r in results if r[0] == s))
print("\nFAIL/WARN items:")
for s, n, d in results:
    if s != "PASS":
        print(f" - [{s}] {n} {('-> ' + d) if d else ''}")
