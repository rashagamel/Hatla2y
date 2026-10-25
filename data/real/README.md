# data/real/

Real, anonymized exports go here (brokerage CSVs, Kaggle raw downloads via
`scripts/ingest_kaggle.py`) — nothing in this folder is committed to
version control by default (see `.gitignore`), since even anonymized real
data generally shouldn't live in a public repo.

Before anything in this folder gets ingested into `data/processed/copilot.db`,
it must pass `scripts/validate_data.py`. See `docs/DATA_COLLECTION.md` for
the full process.

Currently empty in this build — no real data has been collected yet.
