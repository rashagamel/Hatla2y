"""
Builds a ready-to-use database: creates the tables, loads the property listings from the CSV when the
database has none, and removes duplicate listings. Safe to run again: it does nothing once properties exist.

  py scripts\\prepare_db.py                       # uses COPILOT_DB_PATH and data/real/egypt_real_estate_listings.csv
  py scripts\\prepare_db.py --csv path\\to\\file.csv

Used as part of the Render build command, so the deployed server starts with real inventory already loaded.
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_CSV = os.path.join(ROOT, "data", "real", "egypt_real_estate_listings.csv")
sys.path.insert(0, os.path.join(ROOT, "app"))


def property_count(db):
    conn = db.get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=os.environ.get("TASAHEL_INGEST_CSV", DEFAULT_CSV))
    args = ap.parse_args()

    import db
    if not os.path.exists(str(db.DB_PATH)):
        os.makedirs(os.path.dirname(os.path.abspath(str(db.DB_PATH))), exist_ok=True)
        db.init_db()
    else:
        db.migrate_schema()
    print(f"[prepare_db] database: {db.DB_PATH}")

    existing = property_count(db)
    if existing:
        print(f"[prepare_db] {existing} properties already loaded, nothing to do.")
        return
    if not os.path.exists(args.csv):
        sys.exit(f"[prepare_db] CSV not found: {args.csv}")

    env = dict(os.environ, COPILOT_DB_PATH=str(db.DB_PATH))
    print("[prepare_db] loading properties from", args.csv)
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "ingest_kaggle.py"), "--csv", args.csv],
                   check=True, env=env, cwd=ROOT)
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "dedupe_properties.py"),
                    "--db", str(db.DB_PATH), "--apply", "--no-backup"], check=True, env=env, cwd=ROOT)
    total = property_count(db)
    if total == 0:
        sys.exit("[prepare_db] no properties were loaded.")
    print(f"[prepare_db] done: {total} properties ready.")


if __name__ == "__main__":
    main()
