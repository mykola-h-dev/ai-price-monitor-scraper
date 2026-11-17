"""Command-line entry point.

Catalogue mode (CSS selectors, no key needed):
  python -m scraper.main --pages 2                 # 2 listing pages (40 books)
  python -m scraper.main --pages 2 --details       # also open each product page (UPC, category, stock count)
  python -m scraper.main --pages 0 --push          # all pages, then upsert into the Google Sheet

Plain-English fields mode (any URL; AI with a key, selector fallback without):
  python -m scraper.main --url https://example.com/shop --fields "name, price as number, in stock as bool"

Every run: robots.txt check, polite delay + retries, change detection against the previous
run, alert to the outbox (Slack only if SLACK_WEBHOOK_URL is set) and an HTML report.
"""

import argparse
import csv
import json
import logging
import os
import pathlib
import sys
from datetime import date, datetime
from urllib.parse import urlsplit

from . import ai_extract
from .changes import detect_changes, load_snapshot, save_snapshot, send_alert
from .export import deduplicate, load_csv, merge_with_previous, push_to_sheet, write_csv
from .fetch import PoliteFetcher, RobotsDisallowed
from .parse import parse_listing, parse_product_details
from .report import render_report

ROOT = pathlib.Path(__file__).resolve().parent.parent
START_URL = "https://books.toscrape.com/catalogue/page-1.html"
EXIT_ROBOTS = 3

log = logging.getLogger("scraper")


def load_dotenv(path=ROOT / ".env"):
    """Load KEY=VALUE lines from the project's own .env file; real environment variables win."""
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Scrape a website into a clean CSV / Google Sheet, with change alerts.")
    p.add_argument("--start-url", "--url", dest="start_url", default=os.environ.get("START_URL", START_URL))
    p.add_argument("--fields", help='plain-English field list, e.g. "title, price as number, in stock as bool"')
    p.add_argument("--no-ai", action="store_true", help="with --fields: force the offline selector mode")
    p.add_argument("--show-schema", action="store_true", help="with --fields: print the JSON Schema and exit")
    p.add_argument("--pages", type=int, default=2, help="listing pages to scrape (0 = all)")
    p.add_argument("--details", action="store_true", help="visit each product page for UPC/category/stock")
    p.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    p.add_argument("--retries", type=int, default=3, help="attempts per page for timeouts / 429 / 5xx")
    p.add_argument("--out", type=pathlib.Path, default=None,
                   help="CSV to write (default output/books.csv, or output/extracted.csv with --fields)")
    p.add_argument("--report", type=pathlib.Path, default=None, help="HTML report (default: <out dir>/report.html)")
    p.add_argument("--outbox", type=pathlib.Path, default=None, help="alert outbox (default: <out dir>/outbox.log)")
    p.add_argument("--push", action="store_true", help="upsert into the Google Sheet (needs SHEETS_WEBAPP_URL)")
    args = p.parse_args(argv)
    if args.out is None:
        args.out = ROOT / "output" / ("extracted.csv" if args.fields else "books.csv")
    args.report = args.report or args.out.parent / "report.html"
    args.outbox = args.outbox or args.out.parent / "outbox.log"
    return args


def scrape(fetcher, start_url, max_pages, with_details):
    """Follow 'next' links. Returns (rows, complete) - complete=True when the last page was reached."""
    rows, url, page = [], start_url, 0
    while url and (max_pages == 0 or page < max_pages):
        html = fetcher.get(url)
        if html is None:
            break
        page += 1
        products, url = parse_listing(html, url)
        log.info("Page %d: %d products", page, len(products))
        for product in products:
            if with_details:
                try:
                    detail_html = fetcher.get(product["url"])
                except RobotsDisallowed as exc:
                    log.warning("Skipped details: %s", exc)
                    detail_html = None
                if detail_html:
                    product.update(parse_product_details(detail_html))
            rows.append(product)
    return rows, url is None


def track_changes(args, rows, key, complete, columns, meta):
    """Compare with the previous snapshot, alert, write the HTML report, save the new snapshot."""
    snapshot_path = args.out.with_suffix(".snapshot.json")
    changes = detect_changes(load_snapshot(snapshot_path), rows, key=key, complete=complete)
    alert = send_alert(changes, meta["source"], args.outbox)
    if changes.baseline:
        log.info("First run for %s: baseline snapshot saved, no alert", args.out.name)
    elif alert:
        log.info("Changes found, alert written to %s:\n%s", args.outbox, alert)
    else:
        log.info("No changes since the previous run")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_report(rows, changes, columns, meta, key=key), encoding="utf-8")
    log.info("HTML report: %s", args.report)
    save_snapshot(snapshot_path, rows, key=key, source=meta["source"])
    return changes


def run_catalogue(args, fetcher):
    rows, complete = scrape(fetcher, args.start_url, args.pages, args.details)
    fresh = deduplicate(rows)
    merged, added = merge_with_previous(load_csv(args.out), fresh, date.today().isoformat())
    write_csv(args.out, merged)
    log.info("Scraped %d unique products (%d new). CSV now has %d rows: %s", len(fresh), added, len(merged), args.out)
    columns = ["title", "price", "currency", "rating", "in_stock"]
    columns += ["category", "stock_count"] if args.details else []
    meta = _meta(args, "catalogue (CSS selectors)", f"{len(fresh)} items, crawl {'complete' if complete else 'partial'}")
    track_changes(args, fresh, "url", complete, columns + ["url"], meta)
    return merged


def run_fields(args, fetcher):
    fields = ai_extract.parse_fields(args.fields)
    html = fetcher.get(args.start_url)
    if html is None:
        log.error("Page not found (404): %s", args.start_url)
        return None
    result = ai_extract.extract(html, args.start_url, fields, use_ai=not args.no_ai)
    log.info("Extraction mode: %s - %d rows", result.mode, len(result.rows))
    for warning in result.warnings:
        log.warning(warning)
    names = [f.name for f in fields]
    _write_plain_csv(args.out, names, result.rows)
    args.out.with_suffix(".json").write_text(
        json.dumps({"url": args.start_url, "mode": result.mode, "schema": ai_extract.json_schema(fields),
                    "items": result.rows}, indent=1, ensure_ascii=False), encoding="utf-8")
    log.info("Wrote %s and %s", args.out, args.out.with_suffix(".json").name)
    key = _key_field(fields)
    meta = _meta(args, result.mode, f"fields: {', '.join(f'{f.name} ({f.type})' for f in fields)}")
    meta["warnings"] = result.warnings
    track_changes(args, result.rows, key, True, names, meta)
    return result.rows


def _key_field(fields):
    """Rows are matched between runs on the first URL field, else on the first field."""
    return next((f.name for f in fields if f.type == "url"), fields[0].name)


def _meta(args, mode, detail):
    return {"source": args.start_url, "host": urlsplit(args.start_url).netloc, "mode": mode, "detail": detail,
            "run at": datetime.now().strftime("%Y-%m-%d %H:%M")}


def _write_plain_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: ("TRUE" if v is True else "FALSE" if v is False else "" if v is None else v)
                             for c, v in row.items()})


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()
    args = parse_args(argv)
    if args.fields is not None:
        try:
            ai_extract.parse_fields(args.fields)
        except ValueError as exc:
            log.error("%s", exc)
            return 2
    if args.fields and args.show_schema:
        print(json.dumps(ai_extract.json_schema(ai_extract.parse_fields(args.fields)), indent=2))
        return 0
    fetcher = PoliteFetcher(delay_seconds=args.delay, retries=args.retries)
    try:
        if args.fields:
            parsed = ai_extract.parse_fields(args.fields)
            rows, columns, key = run_fields(args, fetcher), [f.name for f in parsed], _key_field(parsed)
        else:
            rows, columns, key = run_catalogue(args, fetcher), None, None
    except RobotsDisallowed as exc:
        log.error("Skipped: %s. Nothing was downloaded. Ask the site owner for permission or use their API.", exc)
        return EXIT_ROBOTS
    if rows is None:
        return 1

    if args.push:
        url, secret = os.environ.get("SHEETS_WEBAPP_URL"), os.environ.get("SHEETS_SHARED_SECRET", "")
        if not url:
            log.error("--push needs SHEETS_WEBAPP_URL (see .env.example); CSV was still written.")
            return 2
        extra = {"columns": columns, "key": key} if columns else {}
        result = push_to_sheet(url, secret, rows, **extra)
        log.info("Google Sheet updated: %d added, %d updated", result["added"], result["updated"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
