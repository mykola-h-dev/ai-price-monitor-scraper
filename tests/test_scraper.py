"""Offline tests (no network): python -m unittest discover tests"""

import pathlib
import unittest

from scraper.clean import parse_availability, parse_price, parse_rating
from scraper.export import deduplicate, merge_with_previous
from scraper.parse import parse_listing

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "listing.html"
PAGE_URL = "https://books.toscrape.com/catalogue/page-2.html"


class CleaningTests(unittest.TestCase):
    def test_price(self):
        self.assertEqual(parse_price("£51.77"), (51.77, "GBP"))
        self.assertEqual(parse_price("Â£1,051.77"), (1051.77, "GBP"))
        self.assertEqual(parse_price("n/a"), (None, ""))

    def test_rating(self):
        self.assertEqual(parse_rating(["star-rating", "Three"]), 3)
        self.assertIsNone(parse_rating(["star-rating"]))

    def test_availability(self):
        self.assertEqual(parse_availability("In stock (22 available)"), (True, 22))
        self.assertEqual(parse_availability(" In stock "), (True, None))
        self.assertEqual(parse_availability("Out of stock"), (False, 0))


class ParseTests(unittest.TestCase):
    def test_listing_page(self):
        products, next_url = parse_listing(FIXTURE.read_text(encoding="utf-8"), PAGE_URL)
        self.assertEqual(next_url, "https://books.toscrape.com/catalogue/page-3.html")
        first, second = products
        self.assertEqual(first["title"], "Sample Book One: The Long Title")
        self.assertEqual((first["price"], first["rating"], first["in_stock"]), (1051.77, 4, True))
        self.assertEqual(first["url"], "https://books.toscrape.com/catalogue/sample-book-one_1/index.html")
        self.assertEqual(second["title"], "Sample Book Two")
        self.assertEqual((second["price"], second["rating"], second["in_stock"]), (9.5, 1, False))


class MergeTests(unittest.TestCase):
    def test_dedupe_and_merge_keeps_first_seen(self):
        rows = deduplicate([{"url": "a", "title": "A", "price": 1}, {"url": "a", "title": "A", "price": 2}])
        self.assertEqual(len(rows), 1)
        previous = [{"url": "a", "title": "A", "price": "1", "upc": "X1", "first_seen": "2026-01-01", "last_seen": "2026-01-01"}]
        merged, added = merge_with_previous(previous, rows + [{"url": "b", "title": "B", "price": 3}], "2026-01-02")
        self.assertEqual(added, 1)
        a = next(r for r in merged if r["url"] == "a")
        self.assertEqual((a["price"], a["upc"], a["first_seen"], a["last_seen"]), (2, "X1", "2026-01-01", "2026-01-02"))


if __name__ == "__main__":
    unittest.main()
