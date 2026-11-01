"""
Removes duplicate property listings (same title, compound, location, cash price, area and bedrooms
within the same data_source), keeping the oldest row of each group.

  py scripts\\dedupe_properties.py            # dry run: only reports what it would do
  py scripts\\dedupe_properties.py --apply    # backs the database up, then removes the duplicates

Uses the database in COPILOT_DB_PATH (or --db). Every table that points at properties is handled:
derived data (installment_plans, matches) of a removed copy is deleted, anything else that references it
(for example outcomes) is re-pointed to the kept row so no history is lost.
"""
import argparse
import os
import shutil
import sqlite3
import sys
import time

KEY = ("data_source", "title", "compound", "location", "cash_price", "area_sqm", "bedrooms")
DELETE_CHILD_TABLES = {"installment_plans", "matches"}     # derived data: recomputed for the kept row


def find_duplicates(conn):
    cols = ", ".join(KEY)
    groups = conn.execute(f"SELECT MIN(id) keep_id, GROUP_CONCAT(id) ids FROM properties "
                          f"GROUP BY {cols} HAVING COUNT(*) > 1").fetchall()
    mapping = {}                       # duplicate id -> kept id
    for keep_id, ids in groups:
        for dup in (int(i) for i in ids.split(",")):
            if dup != keep_id:
                mapping[dup] = keep_id
    return mapping


def referencing_tables(conn):
    refs = []
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        for fk in conn.execute(f"PRAGMA foreign_key_list({table})"):
            if fk[2] == "properties":
                refs.append((table, fk[3]))              # (table, column)
    # tables without a declared foreign key but with the conventional column name
    known = {t for t, _ in refs}
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        if table in known or table == "properties":
            continue
        if any(c[1] == "property_id" for c in conn.execute(f"PRAGMA table_info({table})")):
            refs.append((table, "property_id"))
    return refs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("COPILOT_DB_PATH"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-backup", action="store_true", help="skip the backup copy (used on throwaway build databases)")
    args = ap.parse_args()
    if not args.db or not os.path.exists(args.db):
        sys.exit("Database not found. Set COPILOT_DB_PATH or pass --db <file>.")

    conn = sqlite3.connect(args.db)
    total = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    mapping = find_duplicates(conn)
    refs = referencing_tables(conn)
    print(f"properties: {total}   duplicate copies: {len(mapping)}   would remain: {total - len(mapping)}")
    print("tables referencing properties:", ", ".join(f"{t}.{c}" for t, c in refs) or "none")
    if not mapping:
        print("Nothing to do.")
        return
    if not args.apply:
        print("Dry run only. Run again with --apply to remove them.")
        return

    if not args.no_backup:
        backup = f"{args.db}.backup-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copyfile(args.db, backup)
        print("backup written:", backup)

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("CREATE TEMP TABLE dup_map (dup_id INTEGER PRIMARY KEY, keep_id INTEGER)")
    conn.executemany("INSERT INTO dup_map VALUES (?, ?)", mapping.items())
    if any(t == "installment_plans" for t, _ in refs):
        # Keep the financing data: if the kept listing has no plans but one of its copies does,
        # hand that copy's plans to the kept listing before the copies are removed.
        moved = 0
        for dup_id, keep_id in mapping.items():
            has_plans = conn.execute("SELECT 1 FROM installment_plans WHERE property_id = ? LIMIT 1",
                                     (keep_id,)).fetchone()
            if not has_plans:
                moved += conn.execute("UPDATE installment_plans SET property_id = ? WHERE property_id = ?",
                                      (keep_id, dup_id)).rowcount
        print(f"  installment_plans: moved {moved} plans from copies onto kept listings that had none")
    for table, col in refs:
        if table in DELETE_CHILD_TABLES:
            n = conn.execute(f"DELETE FROM {table} WHERE {col} IN (SELECT dup_id FROM dup_map)").rowcount
            print(f"  {table}: deleted {n} rows that belonged to removed copies")
        else:
            n = conn.execute(f"UPDATE {table} SET {col} = (SELECT keep_id FROM dup_map WHERE dup_id = {table}.{col}) "
                             f"WHERE {col} IN (SELECT dup_id FROM dup_map)").rowcount
            print(f"  {table}: re-pointed {n} rows to the kept listing")
    removed = conn.execute("DELETE FROM properties WHERE id IN (SELECT dup_id FROM dup_map)").rowcount
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    left = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    conn.execute("VACUUM")
    conn.close()
    print(f"removed {removed} duplicate listings; {left} properties remain.")


if __name__ == "__main__":
    main()
