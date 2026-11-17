"""Command-line entry point.

Examples:
  python -m scraper.main --pages 2                 # quick run, 2 listing pages (40 books)
  python -m scraper.main --pages 2 --details       # also open each product page (UPC, category, stock count)
  python -m scraper.main --pages 0 --push          # all pages, then upsert into the Google Sheet
"""

import argparse
import logging
import os
import pathlib
import sys
from datetime import date

from .export import deduplicate, load_csv, merge_with_previous, push_to_sheet, write_csv
from .fetch import PoliteFetcher
from .parse import parse_listing, parse_product_details

ROOT = pathlib.Path(__file__).resolve().parent.parent
START_URL = "https://books.toscrape.com/catalogue/page-1.html"

log = logging.getLogger("scraper")


def load_dotenv(path=ROOT / ".env"):
    """Load KEY=VALUE lines from a local .env file; real environment variables win."""
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Scrape a product catalogue into a clean CSV / Google Sheet.")
    p.add_argument("--start-url", default=os.environ.get("START_URL", START_URL))
    p.add_argument("--pages", type=int, default=2, help="listing pages to scrape (0 = all)")
    p.add_argument("--details", action="store_true", help="visit each product page for UPC/category/stock")
    p.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    p.add_argument("--out", type=pathlib.Path, default=ROOT / "output" / "books.csv")
    p.add_argument("--push", action="store_true", help="upsert into the Google Sheet (needs SHEETS_WEBAPP_URL)")
    return p.parse_args(argv)


def scrape(fetcher, start_url, max_pages, with_details):
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
                detail_html = fetcher.get(product["url"])
                if detail_html:
                    product.update(parse_product_details(detail_html))
            rows.append(product)
    return rows


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()
    args = parse_args(argv)
    fetcher = PoliteFetcher(delay_seconds=args.delay)

    fresh = deduplicate(scrape(fetcher, args.start_url, args.pages, args.details))
    rows, added = merge_with_previous(load_csv(args.out), fresh, date.today().isoformat())
    write_csv(args.out, rows)
    log.info("Scraped %d unique products (%d new). CSV now has %d rows: %s", len(fresh), added, len(rows), args.out)

    if args.push:
        url, secret = os.environ.get("SHEETS_WEBAPP_URL"), os.environ.get("SHEETS_SHARED_SECRET", "")
        if not url:
            log.error("--push needs SHEETS_WEBAPP_URL (see .env.example); CSV was still written.")
            return 2
        result = push_to_sheet(url, secret, rows)
        log.info("Google Sheet updated: %d added, %d updated", result["added"], result["updated"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
