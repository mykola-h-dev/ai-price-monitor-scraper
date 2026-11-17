"""Polite HTTP fetching: identifies itself, respects robots.txt, waits between requests, retries."""

import logging
import time
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

import requests

log = logging.getLogger("scraper")

USER_AGENT = "CatalogScraperDemo/1.0 (portfolio demo; polite crawler)"


class PoliteFetcher:
    def __init__(self, delay_seconds=1.5, retries=3, timeout=20):
        self.delay = delay_seconds
        self.retries = retries
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._last_request = 0.0
        self._robots = {}

    def allowed(self, url):
        """Check the site's robots.txt (a missing robots.txt means everything is allowed)."""
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            parser = robotparser.RobotFileParser()
            try:
                resp = self.session.get(urljoin(base, "/robots.txt"), timeout=self.timeout)
                parser.parse(resp.text.splitlines() if resp.status_code == 200 else [])
            except requests.RequestException:
                parser.parse([])
            self._robots[base] = parser
        return self._robots[base].can_fetch(USER_AGENT, url)

    def get(self, url):
        """Return the page HTML, or None if the page does not exist (404)."""
        if not self.allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        for attempt in range(1, self.retries + 1):
            self._wait_politely()
            try:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                resp.encoding = "utf-8"  # the practice site serves UTF-8 (prices use the pound sign)
                return resp.text
            except requests.RequestException as exc:
                if attempt == self.retries:
                    raise
                log.warning("GET %s failed (%s), retry %d/%d", url, exc, attempt, self.retries - 1)
                time.sleep(2 ** attempt)

    def _wait_politely(self):
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()
