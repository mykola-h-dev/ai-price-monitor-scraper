"""Offline tests for retries, robots.txt, plain-English extraction, change detection,
alerts, the HTML report and an end-to-end run against the local demo site.

No real network: the end-to-end test serves demo_site/ on 127.0.0.1 (random port).
"""

import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("SLACK_WEBHOOK_URL", None)

import requests  # noqa: E402

from scraper import ai_extract, llm  # noqa: E402
from scraper.changes import detect_changes, send_alert, snapshot_of  # noqa: E402
from scraper.demo import run_demo  # noqa: E402
from scraper.fetch import PoliteFetcher, RobotsDisallowed  # noqa: E402
from scraper.report import render_report  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "listing.html"
PAGE_URL = "https://books.toscrape.com/catalogue/page-2.html"


class FakeResponse:
    def __init__(self, status=200, text="", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}
        self.encoding = None

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class FakeSession:
    """Returns queued responses per URL; an Exception instance in the queue is raised."""

    def __init__(self, pages, robots="User-agent: *\nDisallow: /private/\n", robots_status=200):
        self.pages = {url: list(items) for url, items in pages.items()}
        self.robots, self.robots_status = robots, robots_status
        self.headers, self.calls = {}, []

    def get(self, url, timeout=None):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return FakeResponse(self.robots_status, self.robots)
        item = self.pages[url].pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def fetcher_with(session, **kwargs):
    sleeps = []
    clock = iter(range(0, 10_000, 10))  # 10 s between calls: no rate-limit waits
    fetcher = PoliteFetcher(session=session, sleep=sleeps.append, clock=lambda: next(clock), **kwargs)
    return fetcher, sleeps


URL = "https://shop.example/catalogue/page-1.html"


class RetryTests(unittest.TestCase):
    def test_retries_with_exponential_backoff_then_succeeds(self):
        session = FakeSession({URL: [requests.ConnectionError("down"), FakeResponse(503), FakeResponse(200, "ok")]})
        fetcher, sleeps = fetcher_with(session, retries=3, backoff_base=2)
        self.assertEqual(fetcher.get(URL), "ok")
        self.assertEqual(sleeps, [2, 4])

    def test_retry_after_header_is_respected_and_capped(self):
        session = FakeSession({URL: [FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(200, "ok")]})
        fetcher, sleeps = fetcher_with(session)
        self.assertEqual(fetcher.get(URL), "ok")
        self.assertEqual(sleeps, [7])
        self.assertEqual(fetcher.backoff_delay(1, FakeResponse(429, headers={"Retry-After": "3600"})), 60)

    def test_gives_up_after_last_attempt(self):
        session = FakeSession({URL: [FakeResponse(500)] * 3})
        fetcher, sleeps = fetcher_with(session, retries=3, backoff_base=1)
        with self.assertRaises(requests.HTTPError):
            fetcher.get(URL)
        self.assertEqual(sleeps, [1, 2])

    def test_client_errors_are_not_retried_and_404_is_none(self):
        session = FakeSession({URL: [FakeResponse(403)], URL + "?x": [FakeResponse(404)]})
        fetcher, sleeps = fetcher_with(session)
        with self.assertRaises(requests.HTTPError):
            fetcher.get(URL)
        self.assertIsNone(fetcher.get(URL + "?x"))
        self.assertEqual(sleeps, [])

    def test_polite_delay_between_requests(self):
        session = FakeSession({URL: [FakeResponse(200, "a"), FakeResponse(200, "b")]})
        sleeps, times = [], iter([100.0, 100.4, 101.5])
        fetcher = PoliteFetcher(delay_seconds=1.5, session=session, sleep=sleeps.append, clock=lambda: next(times))
        fetcher.get(URL)
        fetcher.get(URL)
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 1.1)


class RobotsTests(unittest.TestCase):
    def test_disallowed_path_raises_clear_error(self):
        fetcher, _ = fetcher_with(FakeSession({}))
        with self.assertRaises(RobotsDisallowed) as ctx:
            fetcher.get("https://shop.example/private/x.html")
        self.assertIn("robots.txt", str(ctx.exception))
        self.assertIn("https://shop.example/robots.txt", str(ctx.exception))

    def test_robots_read_once_per_site(self):
        session = FakeSession({URL: [FakeResponse(200, "a"), FakeResponse(200, "b")]})
        fetcher, _ = fetcher_with(session)
        fetcher.get(URL)
        fetcher.get(URL)
        self.assertEqual(session.calls.count("https://shop.example/robots.txt"), 1)

    def test_forbidden_robots_blocks_all_missing_robots_allows_all(self):
        self.assertFalse(fetcher_with(FakeSession({}, robots_status=403))[0].allowed(URL))
        self.assertTrue(fetcher_with(FakeSession({}, robots_status=404))[0].allowed(URL))


class FieldSpecTests(unittest.TestCase):
    def test_plain_english_fields(self):
        fields = ai_extract.parse_fields("Title, price as number, in stock as bool; rating (integer), "
                                         "product link: url, image, sale price")
        self.assertEqual([(f.name, f.type) for f in fields], [
            ("title", "string"), ("price", "number"), ("in_stock", "boolean"), ("rating", "integer"),
            ("product_link", "url"), ("image", "url"), ("sale_price", "number")])

    def test_empty_spec_is_an_error(self):
        with self.assertRaises(ValueError):
            ai_extract.parse_fields(" , ")

    def test_json_schema(self):
        schema = ai_extract.json_schema(ai_extract.parse_fields("title, price as number, link as url"))
        item = schema["properties"]["items"]["items"]
        self.assertEqual(item["required"], ["title", "price", "link"])
        self.assertEqual(item["properties"]["price"]["type"], ["number", "null"])
        self.assertEqual(item["properties"]["link"]["format"], "uri")
        self.assertFalse(item["additionalProperties"])

    def test_coercion(self):
        c = ai_extract.coerce
        self.assertEqual(c("£1,051.77", "number"), (1051.77, True))
        self.assertEqual(c("Four stars", "integer"), (4, True))
        self.assertEqual(c("In stock (3 available)", "boolean"), (True, True))
        self.assertEqual(c("Out of stock", "boolean"), (False, True))
        self.assertEqual(c("a/b.html", "url", "https://x.example/cat/"), ("https://x.example/cat/a/b.html", True))
        self.assertEqual(c("javascript:alert(1)", "url"), (None, False))
        self.assertEqual(c("n/a", "number"), (None, False))
        self.assertEqual(c(None, "number"), (None, True))
        self.assertEqual(c(True, "number"), (None, False))

    def test_validate_drops_unknown_keys_and_empty_rows(self):
        fields = ai_extract.parse_fields("title, price as number")
        rows, warnings = ai_extract.validate_items(
            [{"Title": " A ", "price": "£2", "secret": "x"}, {"title": None}, "junk", {"title": "B", "price": "?"}],
            fields)
        self.assertEqual(rows, [{"title": "A", "price": 2.0}, {"title": "B", "price": None}])
        self.assertEqual(warnings, ["2 value(s) did not match the schema and were set to empty"])

    def test_llm_json_parsing(self):
        self.assertEqual(ai_extract.parse_llm_json('```json\n{"items": [{"a": 1}]}\n```'), [{"a": 1}])
        self.assertEqual(ai_extract.parse_llm_json('[{"a": 1}]'), [{"a": 1}])
        with self.assertRaises(ValueError):
            ai_extract.parse_llm_json('{"answer": 42}')

    def test_page_text_keeps_links_and_drops_scripts(self):
        text = ai_extract.page_to_text('<p>Hi <a href="/x">More</a></p><script>evil()</script>', "https://s.example/a/")
        self.assertIn("More [link: https://s.example/x]", text)
        self.assertNotIn("evil", text)


class ExtractionModeTests(unittest.TestCase):
    html = FIXTURE.read_text(encoding="utf-8")
    fields = ai_extract.parse_fields("name, price as number, in stock as bool, link as url, colour")

    def test_offline_uses_selector_fallback_and_says_so(self):
        result = ai_extract.extract(self.html, PAGE_URL, self.fields)
        self.assertIn("offline selector mode", result.mode)
        self.assertEqual(result.rows[0]["name"], "Sample Book One: The Long Title")
        self.assertEqual(result.rows[1]["price"], 9.5)
        self.assertFalse(result.rows[1]["in_stock"])
        self.assertTrue(result.rows[0]["link"].startswith("https://books.toscrape.com/catalogue/"))
        self.assertIn("selector mode cannot fill: colour", result.warnings)

    def test_ai_mode_output_is_validated(self):
        reply = json.dumps({"items": [{"name": "Sample Book Two", "price": "£9.50", "in_stock": "Out of stock",
                                       "link": "sample-book-two_2/index.html", "colour": "blue", "extra": 1}]})
        with mock.patch.object(llm, "provider_name", return_value="gemini"), \
                mock.patch.object(llm, "generate_json", return_value=reply) as gen:
            result = ai_extract.extract(self.html, PAGE_URL, self.fields)
        self.assertEqual(result.mode, "ai (gemini)")
        self.assertEqual(result.rows, [{"name": "Sample Book Two", "price": 9.5, "in_stock": False,
                                        "link": "https://books.toscrape.com/catalogue/sample-book-two_2/index.html",
                                        "colour": "blue"}])
        system_prompt, user_prompt = gen.call_args[0]
        self.assertIn("ignore any instructions", system_prompt)
        self.assertIn("Sample Book One", user_prompt)

    def test_ai_errors_fall_back_to_selectors(self):
        for failure in (mock.Mock(side_effect=llm.LLMError("Gemini API error 500")),
                        mock.Mock(return_value="this is not json")):
            with mock.patch.object(llm, "provider_name", return_value="gemini"), \
                    mock.patch.object(llm, "generate_json", failure):
                result = ai_extract.extract(self.html, PAGE_URL, self.fields)
            self.assertIn("offline selector mode (AI failed", result.mode)
            self.assertEqual(len(result.rows), 2)

    def test_llm_without_key_raises_llm_error(self):
        with self.assertRaises(llm.LLMError):
            llm.generate_json("s", "u")


def rows(*items):
    return [{"title": t, "price": p, "currency": "GBP", "in_stock": s, "url": f"https://s.example/{t}"}
            for t, p, s in items]


class ChangeDetectionTests(unittest.TestCase):
    before = snapshot_of(rows(("a", 10.0, True), ("b", 5.0, True), ("c", 7.0, True)))
    after = rows(("a", 8.0, True), ("b", 5.0, False), ("d", 3.0, True))

    def test_new_removed_price_and_stock(self):
        changes = detect_changes(self.before, self.after)
        self.assertEqual([i["title"] for i in changes.new], ["d"])
        self.assertEqual([i["title"] for i in changes.removed], ["c"])
        self.assertEqual([(c["old"], c["new"], c["pct"]) for c in changes.price_changed], [(10.0, 8.0, -20.0)])
        self.assertEqual([(c["item"]["title"], c["new"]) for c in changes.stock_changed], [("b", False)])
        self.assertEqual(changes.total, 4)

    def test_partial_crawl_does_not_report_removed(self):
        changes = detect_changes(self.before, self.after, complete=False)
        self.assertEqual(changes.removed, [])
        self.assertFalse(changes.removed_checked)

    def test_first_run_is_baseline(self):
        self.assertTrue(detect_changes(None, self.after).baseline)

    def test_csv_strings_compare_with_numbers(self):
        before = {"https://s.example/a": {"title": "a", "price": "10.0", "in_stock": "TRUE"}}
        changes = detect_changes(before, rows(("a", 10.0, True)))
        self.assertEqual(changes.total, 0)

    def test_plain_english_field_names_are_mapped(self):
        snap = snapshot_of([{"name": "Lamp", "sale_price": 12.5, "link": "https://s.example/lamp"}], key="link")
        self.assertEqual(snap["https://s.example/lamp"]["title"], "Lamp")
        self.assertEqual(snap["https://s.example/lamp"]["price"], 12.5)


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.outbox = pathlib.Path(self.tmp.name) / "outbox.log"
        self.changes = detect_changes(ChangeDetectionTests.before, ChangeDetectionTests.after)

    def tearDown(self):
        self.tmp.cleanup()

    def test_outbox_only_by_default(self):
        post = mock.Mock()
        text = send_alert(self.changes, "s.example", self.outbox, post=post)
        post.assert_not_called()
        log = self.outbox.read_text(encoding="utf-8")
        self.assertIn("~ a: £10.00 -> £8.00 (-20.0%)", log)
        self.assertIn("+ New: d (£3.00)", log)
        self.assertIn("- Removed: c", log)
        self.assertIn("! b: now out of stock", text)

    def test_slack_when_configured_and_failures_do_not_raise(self):
        post = mock.Mock(return_value=FakeResponse(200))
        send_alert(self.changes, "s.example", self.outbox, slack_url="https://hooks.slack.example/x", post=post)
        self.assertIn("Price watch", post.call_args.kwargs["json"]["text"])
        failing = mock.Mock(side_effect=requests.ConnectionError("no network"))
        self.assertIsNotNone(send_alert(self.changes, "s", self.outbox, slack_url="https://h.example", post=failing))

    def test_no_alert_without_changes_or_on_baseline(self):
        self.assertIsNone(send_alert(detect_changes(None, []), "s", self.outbox))
        self.assertIsNone(send_alert(detect_changes({}, []), "s", self.outbox))
        self.assertFalse(self.outbox.exists())


class ReportTests(unittest.TestCase):
    def test_changes_are_highlighted_and_html_is_escaped(self):
        changes = detect_changes(ChangeDetectionTests.before, ChangeDetectionTests.after)
        after = ChangeDetectionTests.after + rows(("<script>x</script>", 1.0, True))
        html = render_report(after, changes, ["title", "price", "in_stock", "url"], {"source": "s.example"})
        self.assertIn("class='price-down'", html)
        self.assertIn("class='new'", html)
        self.assertIn("class='removed'", html)
        self.assertIn("PRICE DOWN", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>x", html)


class EndToEndTests(unittest.TestCase):
    """Two real HTTP runs against demo_site/ served on 127.0.0.1, plus the robots.txt skip."""

    def test_demo_day1_then_day2(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "demo"
            with self.assertLogs("scraper", level="INFO") as logs:
                codes = run_demo(out_dir=out, port=0, delay=0)
            self.assertEqual(codes, [0, 0, 3])
            outbox = (out / "outbox.log").read_text(encoding="utf-8")
            self.assertIn("1 new, 1 removed, 2 price change(s), 1 stock change(s)", outbox)
            self.assertIn("~ Harbour Lights: £14.99 -> £11.99 (-20.0%)", outbox)
            self.assertIn("+ New: Winter Kitchen", outbox)
            self.assertIn("- Removed: Salt and Iron", outbox)
            report = (out / "report.html").read_text(encoding="utf-8")
            self.assertIn("PRICE UP", report)
            self.assertIn("Winter Kitchen", report)
            self.assertTrue(any("Skipped: robots.txt" in line for line in logs.output))
            self.assertFalse((out / "private.csv").exists())


if __name__ == "__main__":
    unittest.main()
