"""Save results: merge with the previous CSV (de-duplicated by URL) and optionally push to a Google Sheet."""

import csv
import logging

import requests

log = logging.getLogger("scraper")

COLUMNS = ["title", "price", "currency", "rating", "in_stock", "category", "stock_count", "upc",
           "url", "image_url", "first_seen", "last_seen"]
KEY = "url"


def deduplicate(rows):
    """Keep one row per URL (the last one wins)."""
    unique = {}
    for row in rows:
        unique[row[KEY]] = row
    return list(unique.values())


def load_csv(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def merge_with_previous(previous, fresh, run_date):
    """Update existing rows, add new ones, keep first_seen; rows not seen today keep their old last_seen."""
    merged = {row[KEY]: row for row in previous}
    added = 0
    for row in fresh:
        old = merged.get(row[KEY])
        if old is None:
            added += 1
        if old:
            # keep earlier detail fields (UPC, category...) when today's run did not fetch them
            row = {**old, **{k: v for k, v in row.items() if v not in (None, "")}}
        row["first_seen"] = old["first_seen"] if old else run_date
        row["last_seen"] = run_date
        merged[row[KEY]] = row
    return sorted(merged.values(), key=lambda r: r["title"].lower()), added


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: _cell(row.get(col)) for col in COLUMNS})


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return value


def push_to_sheet(webapp_url, secret, rows, batch_size=200, columns=COLUMNS, key=KEY):
    """Upsert rows into a Google Sheet through apps_script/Code.gs (matched on the key column)."""
    total = {"added": 0, "updated": 0}
    for start in range(0, len(rows), batch_size):
        batch = [{col: _cell(r.get(col)) for col in columns} for r in rows[start:start + batch_size]]
        resp = requests.post(webapp_url, json={"token": secret, "key": key, "columns": columns, "rows": batch},
                             timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "ok":
            raise RuntimeError(f"Sheet web app error: {data.get('message')}")
        total["added"] += data.get("added", 0)
        total["updated"] += data.get("updated", 0)
    return total
