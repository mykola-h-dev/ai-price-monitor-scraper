"""Polite HTTP fetching: identifies itself, respects robots.txt, waits between requests,
retries temporary failures with exponential backoff.

`session`, `sleep` and `clock` can be injected, so the retry and rate-limit logic is
unit-tested without network access and without real waiting.
"""

import logging
import time
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

import requests

log = logging.getLogger("scraper")

USER_AGENT = "CatalogScraperDemo/1.0 (portfolio demo; polite crawler)"
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER = 60  # never wait longer than this for a server's Retry-After header


class RobotsDisallowed(PermissionError):
    """Raised when robots.txt does not allow our User-Agent to fetch a URL."""

    def __init__(self, url, robots_url):
        super().__init__(f"robots.txt ({robots_url}) disallows {url} for User-Agent '{USER_AGENT}'")
        self.url = url
        self.robots_url = robots_url


class TemporaryHTTPError(requests.HTTPError):
    """A status code worth retrying (429 / 5xx)."""


class PoliteFetcher:
    def __init__(self, delay_seconds=1.5, retries=3, timeout=20, backoff_base=2.0,
                 session=None, sleep=time.sleep, clock=time.monotonic):
        self.delay = delay_seconds
        self.retries = max(1, retries)
        self.timeout = timeout
        self.backoff_base = backoff_base
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._sleep = sleep
        self._clock = clock
        self._last_request = None
        self._robots = {}

    # --- robots.txt -------------------------------------------------------
    @staticmethod
    def robots_url(url):
        parts = urlsplit(url)
        return urljoin(f"{parts.scheme}://{parts.netloc}", "/robots.txt")

    def allowed(self, url):
        """Check the site's robots.txt.

        Same rules as urllib's RobotFileParser.read(): 401/403 means nothing is allowed,
        any other missing robots.txt (404, network error) means everything is allowed.
        """
        robots_url = self.robots_url(url)
        if robots_url not in self._robots:
            parser = robotparser.RobotFileParser(robots_url)
            try:
                resp = self.session.get(robots_url, timeout=self.timeout)
                if resp.status_code in (401, 403):
                    parser.disallow_all = True
                else:
                    parser.parse(resp.text.splitlines() if resp.status_code == 200 else [])
            except requests.RequestException:
                parser.parse([])
            self._robots[robots_url] = parser
        return self._robots[robots_url].can_fetch(USER_AGENT, url)

    # --- fetching ---------------------------------------------------------
    def get(self, url):
        """Return the page HTML, or None if the page does not exist (404).

        Raises RobotsDisallowed if robots.txt forbids the URL, and the last error after
        all retries are used up.
        """
        if not self.allowed(url):
            raise RobotsDisallowed(url, self.robots_url(url))
        for attempt in range(1, self.retries + 1):
            self._wait_politely()
            try:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 404:
                    return None
                if resp.status_code in RETRY_STATUSES:
                    raise TemporaryHTTPError(f"HTTP {resp.status_code}", response=resp)
                resp.raise_for_status()  # other 4xx: not worth retrying
                if not _declared_charset(resp):
                    resp.encoding = "utf-8"  # requests would guess ISO-8859-1 and break the pound sign
                return resp.text
            except (requests.ConnectionError, requests.Timeout, TemporaryHTTPError) as exc:
                if attempt == self.retries:
                    raise
                wait = self.backoff_delay(attempt, getattr(exc, "response", None))
                log.warning("GET %s failed (%s), retry %d/%d in %.1f s",
                            url, exc, attempt, self.retries - 1, wait)
                self._sleep(wait)

    def backoff_delay(self, attempt, response=None):
        """Exponential backoff: base, 2*base, 4*base... or the server's Retry-After (capped)."""
        retry_after = response.headers.get("Retry-After") if response is not None else None
        if retry_after and str(retry_after).isdigit():
            return min(float(retry_after), MAX_RETRY_AFTER)
        return self.backoff_base * (2 ** (attempt - 1))

    def _wait_politely(self):
        now = self._clock()
        if self._last_request is not None:
            elapsed = now - self._last_request
            if elapsed < self.delay:
                self._sleep(self.delay - elapsed)
                now = self._clock()
        self._last_request = now


def _declared_charset(resp):
    content_type = (resp.headers or {}).get("Content-Type", "")
    return "charset=" in content_type.lower()
